"""Production authorization scope, preserved weights and monthly recipe guards."""
from copy import deepcopy
import json
import pytest
from app.initial_approval import MODEL, MODEL_SHA, issue, valid, evaluation
from app.model_inputs import FEEDBACK_SCHEMA,contract
from Modelo.operacion.training import DEFAULTS,validate_spec,Histories
from Modelo.operacion.registry import LocalRegistry
from Modelo.operacion.promotion import rollback


def metadata():
    return dict(model_version=MODEL,model_sha256=MODEL_SHA,simulation=False,eligible=False,approved=False,
        schema_version=FEEDBACK_SCHEMA,window=10,contract=contract(10,FEEDBACK_SCHEMA),radius=4.2,
        serving_bus_context='feedback-derived-arrivals-v1',fallback={'rio_consulado':{'global':{'sum':4,'count':1}}},
        scaler={k:dict(mean=[0]*n,scale=[1]*n) for k,n in [('temporal',7),('past_wait',1),('bus_context',4)]},
        effective_parameters=dict(units=16,optimizer='rmsprop',learning_rate=.0003))


def authorized():
    source=metadata();grant=issue(source,operator='authorized-test',reason='Explicit initial release',source_sha256='a'*64)
    return {**source,'approved':True,'initial_release_exception':grant}


def test_exception_preserves_original_eligibility_and_binds_metadata():
    result=authorized();assert valid(result) and result['eligible'] is False
    assert evaluation(metadata(),result['initial_release_exception'])['independent_evaluation_pending']
    for field,value in [('model_version','other'),('model_sha256','b'*64),('radius',0),('approved',False),('simulation',True),('window',20)]:
        changed=deepcopy(result);changed[field]=value;assert not valid(changed)
    for field in ('operator','reason','metadata_sha256'):
        changed=deepcopy(result);changed['initial_release_exception'][field]='';assert not valid(changed)
    assert metadata()['approved'] is False


def test_exception_does_not_skip_model_hash(tmp_path):
    from app.predictor import Predictor
    (tmp_path/'metadata.json').write_text(json.dumps(authorized()));(tmp_path/'model.keras').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):Predictor(tmp_path)
    (tmp_path/'metadata.json').write_text(json.dumps(metadata()))
    with pytest.raises(ValueError,match='aprobado'):Predictor(tmp_path)


def test_monthly_recipe_explicit_no_silent_adam_or_twenty():
    assert validate_spec(DEFAULTS)==DEFAULTS
    for changed in ({},{**DEFAULTS,'window':20},{**DEFAULTS,'optimizer':'adam'},{**DEFAULTS,'learning_rate':.001}):
        with pytest.raises(ValueError):validate_spec(changed)


def test_release_id_accepts_decimal_rate_without_changing_model_identity():
    import re
    from Modelo.promote_job import release_identifier
    value = release_identifier(MODEL)
    assert re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value)
    assert value == release_identifier(MODEL)
    assert value != release_identifier(MODEL.replace('.', '-'))


def test_cloud_run_deploy_uses_short_revision_name():
    from Modelo.operacion.promotion import CloudRunDriver
    driver = object.__new__(CloudRunDriver)
    driver.name = 'projects/p/locations/r/services/arribo-api'
    driver.service = 'arribo-api'
    driver.image = 'registry/api@sha256:' + 'a'*64
    state = dict(etag='etag', template={'containers':[{'env':[]}]}, trafficStatuses=[])
    driver.describe = lambda: deepcopy(state)
    driver.traffic = lambda: {'old':100}
    def patch(body, mask):
        revision = body['template']['revision']
        assert revision.startswith('arribo-api-r') and '/' not in revision and len(revision)<=63
        state['trafficStatuses'] = [dict(revision=revision,tag=body['traffic'][-1]['tag'],uri='https://tagged.invalid')]
    driver.patch = patch
    result = driver.deploy(dict(uri='gs://model/manifest.json',generation=1,sha256='b'*64),'release')
    assert result['url'] == 'https://tagged.invalid'


def test_cloud_job_arguments_keep_flag_values_unambiguous():
    from Infra.prepare import plan
    from pathlib import Path
    config = json.loads((Path(__file__).resolve().parents[2]/'Infra/config.example.json').read_text())
    deployments = [step['command'] for step in plan(config,'jobs') if 'deploy' in step['command']]
    arguments = deployments[0][deployments[0].index('--args')+1].split(',')
    assert '--project=arribo-68326' in arguments
    assert '--confirm-dev-project=arribo-68326' in arguments
    assert len(arguments) == len(set(arguments))


def test_real_feedback_receipt_and_padding_are_causal():
    def row(i,query,received,wait,basis='since_query'):
        return dict(sample_id=str(i),source='feedback',record_type='user_wait',event_at=query,available_at=received,
            target=wait,loss_weight=.5,station_id='rio_consulado',uid=str(i),query_id=str(i),wait_basis=basis,context={},
            continuity_group=['rio_consulado','feedback','train'],split='train')
    rows=[row(1,'2026-11-01T12:00:00Z','2026-11-01T12:06:00Z',5),row(2,'2026-11-01T12:10:00Z','2026-11-01T12:20:00Z',8)]
    builder=Histories({'train':rows,'validation':[]},{})
    inputs=builder.inputs(rows[-1],1,15)
    assert len(inputs['step_mask'])==10 and inputs['past_wait'][-2:]==[[5],[0]]
    assert inputs['bus_context'][-1]==[5,0,0,0]
    assert builder.inputs(rows[-1],0,0)['bus_mask'][-1]==[0]*4
    future=deepcopy(rows[0]);future['available_at']='2026-11-01T12:30:00Z'
    other=Histories({'train':[future,rows[-1]],'validation':[]},{})
    assert other.inputs(rows[-1],1,0)['bus_mask'][-1]==[0]*4


def test_first_release_can_restore_service_without_model(tmp_path):
    registry=LocalRegistry(tmp_path)
    registry.cas('deployment/current',{'previous':None,'previous_traffic':{'baseline':100},'package':{'release_id':'initial'}},0)
    class Driver:
        def traffic(self):return {"new":100}
        def switch(self,value):self.current=value
        def baseline_ready(self):return True
    driver=Driver();result=rollback(registry,driver,enabled=True)
    assert result['predictor_ready'] is False and driver.current=={'baseline':100}
    assert registry.read('deployment/current')[0] is None
    assert registry.read('promotion/suspended')[0]['suspended']


def test_operational_trainer_uses_rmsprop_and_roundtrips(tmp_path,monkeypatch):
    tf=pytest.importorskip('tensorflow')
    from test_operations import camera_file
    from Modelo.operacion.data import prepare_mixed
    from Modelo.operacion.training import train
    from app.artifacts import json_bytes,digest
    from app.predictor import Predictor
    from pathlib import Path
    rows,quality=prepare_mixed(camera_file(tmp_path),[], '2026-10-20T00:00:00Z',window=10,schema_version=FEEDBACK_SCHEMA)
    data=tmp_path/'data';data.mkdir()
    content=b''.join(json_bytes(r)+b'\n' for r in rows)
    (data/'sequences.jsonl').write_bytes(content)
    (data/'quality.json').write_text(json.dumps({**quality,'sequences_sha256':digest(content)}))
    original=tf.keras.Model.fit
    def short(self,*a,**kw):
        kw['epochs']=2
        return original(self,*a,**kw)
    monkeypatch.setattr(tf.keras.Model,'fit',short)
    output=tmp_path/'model'
    m=train(data,output,'operational-unit-test',DEFAULTS)
    assert m['effective_parameters']['optimizer']=='rmsprop' and m['window']==10
    assert m['approved'] is False and m['collection_goal_met'] is False
    assert len(m['training_profiles'])==10 and m['roundtrip_verified']
    import zipfile
    config=json.loads(zipfile.ZipFile(output/'model.keras').read('config.json'))
    assert config['compile_config']['optimizer']['class_name']=='RMSprop'
    candidate=Predictor(output,require_approved=False)
    assert 0<=candidate.predict({},[],now='2026-10-21T12:00:00Z')[0]<=45


def test_initial_model_can_be_promoted_again_after_rollback(tmp_path):
    from test_operations import FakeDriver,promotion_args
    from Modelo.operacion.promotion import promote
    registry=LocalRegistry(tmp_path)
    class Driver(FakeDriver):
        def baseline_ready(self):return self.current=={'old':100}
    driver=Driver();args=promotion_args()
    args.update(expected_incumbent=None,manual_initial=True)
    args['evaluation'].update(decision='manual_initial_approval',publishable=False,incumbent_version=None)
    assert promote(registry,driver,**args)['status']=='active'
    rollback(registry,driver,enabled=True)
    assert registry.read('deployments/r2')[0]['status']=='rolled_back'
    assert promote(registry,driver,**args)['status']=='active'
    assert driver.current=={'new':100}
