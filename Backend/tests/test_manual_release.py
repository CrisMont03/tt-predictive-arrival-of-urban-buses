"""Exact replacement authorization, causal inputs and monthly recipe compatibility."""
from copy import deepcopy
import pytest
from app.manual_release import MODEL, MODEL_SHA, issue, valid, evaluation
from app.model_inputs import NEXT_BUS_SCHEMA,contract,online_inputs
from app.artifacts import digest,json_bytes
from Modelo.operacion.next_bus_training import MONTHLY_DEFAULTS,validate_monthly_spec
from Dataset.next_bus import confirmed_feedback


def original():
    return dict(model_version=MODEL,model_sha256=MODEL_SHA,simulation=False,eligible=False,approved=False,
        schema_version=NEXT_BUS_SCHEMA,window=30,contract=contract(30,NEXT_BUS_SCHEMA),target_definition='next_bus_wait',
        radius=51.04,effective_parameters=dict(units=32,dense_units=16,optimizer='rmsprop',learning_rate=.001))


def previous():
    return dict(model_version='previous',package={'release_id':'previous-release','uri':'gs://models/previous/manifest.json','generation':1,'sha256':'a'*64},
                deployment={'revision':'arribo-api-previous','url':'https://previous.invalid'})


def authorized():
    m=original();grant=issue(m,operator='test',reason='Explicit replacement',source_sha256=digest(json_bytes(m)),previous=previous())
    return {**m,'approved':True,'manual_release_exception':grant}


def test_manual_release_is_scoped_and_keeps_eligibility():
    m=authorized();assert valid(m) and m['eligible'] is False
    result=evaluation(original(),m['manual_release_exception'],'previous')
    assert result['decision']=='manual_replacement_approval' and not result['publishable']
    for field,value in [('window',20),('model_sha256','c'*64),('model_version','future-monthly'),('radius',1),('simulation',True),('approved',False)]:
        other=deepcopy(m);other[field]=value;assert not valid(other)
    with pytest.raises(ValueError):evaluation(original(),m['manual_release_exception'],'different')
    with pytest.raises(ValueError):issue(original(),operator='test',reason='test',source_sha256='a'*64,previous={})


def test_next_bus_monthly_recipe_is_exact_and_unapproved():
    assert validate_monthly_spec(MONTHLY_DEFAULTS)['window']==30
    for field,value in [('window',10),('units',16),('learning_rate',.0003),('optimizer','adam')]:
        with pytest.raises(ValueError):validate_monthly_spec({**MONTHLY_DEFAULTS,field:value})


def test_old_query_feedback_is_not_a_next_bus_training_label():
    r=dict(uid='u',query_id='q',station_id='rio_consulado',simulation=False,wait_basis='since_query',actual_min=5,
           query_at='2026-11-01T12:00:00Z',received_at='2026-11-01T12:06:00Z',context={})
    assert not confirmed_feedback([r],'2026-11-02T00:00:00Z')[0]
    assert not confirmed_feedback([{**r,'prediction_target':'personal_wait'}],'2026-11-02T00:00:00Z')[0]
    assert len(confirmed_feedback([{**r,'prediction_target':'next_bus_wait'}],'2026-11-02T00:00:00Z')[0])==1


def test_thirty_steps_use_backups_and_keep_current_label_missing():
    table={'rio_consulado':{'global':{'sum':60,'count':1}}}
    x=online_inputs({},30,now='2026-11-05T12:00:00Z',fallback=table,schema_version=NEXT_BUS_SCHEMA)
    assert len(x['step_mask'])==30 and sum(p[0] for p in x['step_mask'])==30
    assert x['past_wait'][-1]==[0] and x['past_wait_mask'][-1]==[0]
    assert x['past_wait'][-2]==[60] and x['wait_origin'][-2]==[0,1,0]
    assert all(p==[0]*4 for p in x['bus_mask'])


def test_manual_replacement_requires_flag_and_exact_previous_revision(tmp_path):
    from test_operations import FakeDriver
    from Modelo.operacion.registry import LocalRegistry
    from Modelo.operacion.promotion import promote,rollback
    registry=LocalRegistry(tmp_path/'registry');driver=FakeDriver();prior=previous()
    registry.cas('deployment/current',prior,0)
    grant=authorized()['manual_release_exception'];ev=evaluation(original(),grant,'previous')
    args=dict(package={'release_id':'next','uri':'gs://models/next','generation':1,'sha256':'a'*64},
              evaluation=ev,expected_incumbent='previous',model_version=MODEL,enabled=True)
    with pytest.raises(ValueError,match='mejora'):promote(registry,driver,**args)
    altered=deepcopy(ev);altered['manual_release_exception']['previous']['deployment']['revision']='different'
    with pytest.raises(ValueError,match='cambió'):promote(registry,driver,**{**args,'evaluation':altered},manual_release=True)
    assert promote(registry,driver,**args,manual_release=True)['status']=='active'
    assert rollback(registry,driver,enabled=True)['model_version']=='previous'
    assert driver.current=={'old':100}


def test_monthly_next_bus_trainer_roundtrips_thirty_steps(tmp_path,monkeypatch):
    tf=pytest.importorskip('tensorflow')
    from test_operations import camera_file
    from Dataset.next_bus import prepare_maintenance
    from Modelo.operacion.next_bus_training import train_maintenance
    from app.predictor import Predictor
    from app.artifacts import immutable_json
    import json
    rows,quality=prepare_maintenance(camera_file(tmp_path),[],'2026-10-20T00:00:00Z',continuity_confirmed=True,window=30)
    data=tmp_path/'prepared';data.mkdir()
    (data/'sequences.jsonl').write_text(''.join(json.dumps(r,default=str)+'\n' for r in rows))
    immutable_json(data/'quality.json',quality)
    fit=tf.keras.Model.fit
    def short(self,*args,**kwargs):
        kwargs['epochs']=2
        return fit(self,*args,**kwargs)
    monkeypatch.setattr(tf.keras.Model,'fit',short)
    m=train_maintenance(data,tmp_path/'candidate','unit-next-bus',MONTHLY_DEFAULTS)
    assert m['window']==30 and m['effective_parameters']['units']==32
    assert m['effective_parameters']['optimizer']=='rmsprop' and m['approved'] is False
    predictor=Predictor(tmp_path/'candidate',require_approved=False)
    value,_=predictor.predict({},[],now='2026-10-21T12:00:00Z')
    assert 0<=value<=1440
