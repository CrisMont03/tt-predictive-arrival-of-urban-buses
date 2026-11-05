"""Next-bus goal, continuity, causal inputs and local API compatibility."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from Dataset.next_bus import queries_from_events,valid_camera_reports,confirmed_feedback
from Modelo.next_bus import SequenceBuilder
from app.model_inputs import NEXT_BUS_SCHEMA,NEXT_BUS_MAX_WAIT,FEEDBACK_SCHEMA,contract,online_inputs,timestamp,transform,fit_scaler
from app.predictor import Predictor
from app.prediction_cache import reusable_prediction


def rows():
    events=[]
    for day in ('2025-10-01','2025-10-10','2025-10-15'):
        for h in ('05:00:00','05:10:00','07:00:00','11:30:00'):
            events.append(dict(day=day,arrival_at=day+'T'+h+'-06:00',sources=[],sentinel_rows=1))
    return events


def test_csv_selection_ignores_auxiliary_folders_and_deduplicates_arrivals(tmp_path):
    import csv
    from Dataset.next_bus import load_csvs
    for directory in ('dataset','datasets'):
        folder=tmp_path/'2025_10_01'/directory;folder.mkdir(parents=True)
        with (folder/'sample_v4_dataset.csv').open('w') as f:
            writer=csv.DictWriter(f,fieldnames=['track_id_persona','arrival_user','bus_arrival','waiting_time_min'])
            writer.writeheader()
            for time in ('05:00:00','07:00:00'):
                writer.writerow(dict(track_id_persona=-1,arrival_user='2025-10-01 '+time,bus_arrival='2025-10-01 '+time,waiting_time_min=0))
    extra=tmp_path/'2025_10_01'/'extra-docs';extra.mkdir()
    (extra/'bad_v4_dataset.csv').write_text('not a dataset')
    (tmp_path/'2025_10_01'/'dataset'/'metrics.csv').write_text('not a dataset')
    events,reports,sources=load_csvs(tmp_path)
    assert len(events)==2 and len(sources)==2 and not reports
    assert all(len(e['sources'])==2 for e in events)


def historical(tmp_path):
    events=rows();samples,_,_=queries_from_events(events)
    builder=SequenceBuilder(samples,[])
    scaler={k:dict(mean=[0.]*w,scale=[1.]*w) for k,w in [('temporal',7),('past_wait',1),('bus_context',4)]}
    radius=40.
    payload=json.dumps(dict(fallback=builder.fallback,radius=radius)).encode()
    (tmp_path/'historical.json').write_bytes(payload)
    m=dict(schema_version=NEXT_BUS_SCHEMA,target_definition='next_bus_wait',predictor_kind='historical',
        contract=contract(10,NEXT_BUS_SCHEMA),window=10,scaler=scaler,fallback=builder.fallback,
        historical_sha256=hashlib.sha256(payload).hexdigest(),radius=radius,model_version='historical-unit',
        approved=False,eligible=False,simulation=False,serving_bus_context='feedback-derived-arrivals-v1')
    (tmp_path/'metadata.json').write_text(json.dumps(m))
    return Predictor(tmp_path,require_approved=False)


def test_grid_joins_confirmed_day_and_keeps_long_labels_not_personal_waits():
    q,coverage,_=queries_from_events(rows())
    sample=next(r for r in q if r['event_at']=='2025-10-01T13:00:00+00:00') # 07:00 local, bus now
    assert sample['target']==0
    long=next(r for r in q if r['event_at']=='2025-10-01T13:01:00+00:00')
    assert long['target']==269 and long['record_type']=='derived_next_bus_query'
    assert len(coverage)==3 and all(c['verified'] for c in coverage)
    assert all(timestamp(r['bus_arrival']).date()==timestamp(r['event_at']).date() for r in q)
    assert max(r['event_at'] for r in q).endswith('17:30:00+00:00')
    assert {r['split'] for r in q}=={'train','validation','retrospective'}


def test_fractional_hour_and_unknown_buses_without_feedback():
    at='2026-11-04T17:30:00-06:00'
    x=online_inputs({},10,now=at,schema_version=NEXT_BUS_SCHEMA)
    assert x['temporal'][-1][0]==17.5 and x['bus_mask'][-1]==[0]*4
    assert online_inputs({},10,now=at,schema_version=FEEDBACK_SCHEMA)['temporal'][-1][0]==17
    assert contract(10,FEEDBACK_SCHEMA)['max_wait']==45
    assert contract(10,NEXT_BUS_SCHEMA)['max_wait']==1440


def test_future_labels_and_validation_do_not_change_training_inputs_or_fallback():
    q,_,_=queries_from_events(rows());builder=SequenceBuilder(q,[])
    first=min((r for r in q if r['split']=='train'),key=lambda r:r['event_at'])
    changed=deepcopy(q)
    for r in changed:
        if r['split']!='train':r['target']=999
    other=SequenceBuilder(changed,[])
    assert builder.fallback==other.fallback
    assert builder.inputs(first,'cold')==other.inputs(first,'cold')
    assert sum(x[0] for x in builder.inputs(first,'cold')['step_mask'])==1 # no completed training interval yet
    current=builder.inputs(first,'cold')
    assert current['past_wait'][-1]==[0] and current['past_wait_mask'][-1]==[0]


def test_long_feedback_only_available_after_receipt_and_grids_never_reports():
    report=dict(uid='u',query_id='long',station_id='rio_consulado',simulation=False,wait_basis='since_query',
        query_at='2026-11-04T12:00:00Z',received_at='2026-11-04T13:31:00Z',actual_min=90,context={},prediction_target='next_bus_wait')
    accepted,excluded=confirmed_feedback([report], '2026-11-04T14:00:00Z')
    assert len(accepted)==1 and not excluded
    args=dict(schema_version=NEXT_BUS_SCHEMA,observations=[report])
    before=online_inputs({},10,now='2026-11-04T13:30:00Z',**args)
    after=online_inputs({},10,now='2026-11-04T13:32:00Z',**args)
    assert before['bus_mask'][-1]==[0]*4
    assert after['bus_context'][-1]==[2,0,0,0]
    assert after['past_wait'][-2]==[90]
    assert online_inputs({},10,now='2026-11-04T13:32:00Z',observations=[report],schema_version=FEEDBACK_SCHEMA)['bus_mask'][-1]==[0]*4
    assert confirmed_feedback([{**report,'wait_basis':'unspecified'}], '2026-11-04T14:00:00Z')[0]==[]


def test_full_archive_and_firestore_two_hour_snapshot_build_same_input():
    old=dict(uid='u0',query_id='old',station_id='rio_consulado',simulation=False,actual_min=5,wait_basis='since_query',
             query_at='2026-11-04T12:00:00Z',received_at='2026-11-04T12:05:00Z',context={})
    recent={**old,'uid':'u1','query_id':'recent','actual_min':9,
            'query_at':'2026-11-04T13:00:00Z','received_at':'2026-11-04T13:10:00Z'}
    args=dict(now='2026-11-04T15:00:00Z',schema_version=NEXT_BUS_SCHEMA)
    full=online_inputs({},10,observations=[old,recent],**args)
    snapshot=online_inputs({},10,observations=[recent],**args)
    assert full==snapshot
    assert full['bus_mask'][-2]==[0]*4 and full['bus_context'][-1][0]==111


def test_historical_package_is_unapproved_and_checks_hash(tmp_path):
    p=historical(tmp_path)
    value,interval=p.predict({},[],now='2026-11-04T08:30:00-06:00')
    assert value>45 and interval[0]<=value<=interval[1]
    with pytest.raises(ValueError,match='aprobado'):Predictor(tmp_path)
    (tmp_path/'historical.json').write_text('{}')
    with pytest.raises(ValueError,match='Checksum'):Predictor(tmp_path,require_approved=False)


def test_local_api_historical_cache_feedback_and_long_domain(tmp_path,monkeypatch):
    from test_query_flow import Adapter
    from app.main import create_app
    from app.integration_predictor import integration_context
    monkeypatch.setattr(auth,'verify_id_token',lambda token,**kw:{'uid':token})
    predictor=historical(tmp_path);clock=[timestamp('2026-11-04T08:30:00-06:00')];store=Adapter()
    with TestClient(create_app('production',store,predictor,clock=lambda:clock[0],context_provider=integration_context)) as api:
        def query(qid):return api.post('/predict',headers={'Authorization':'Bearer u'},json={'request_id':qid,'station_id':'rio_consulado'})
        first=query('next-bus-unit-0001');assert first.status_code==200,first.text
        value=first.json();assert value['source']=='historical' and value['waiting_time_min']>45
        assert value['prediction_target']=='next_bus_wait' and value['estimator']=='historical'
        clock[0]+=timedelta(seconds=10)
        second=query('next-bus-unit-0002').json();assert second['source']=='cache'
        assert second['waiting_time_min']==pytest.approx(value['waiting_time_min']-10/60)
        assert second['estimated_arrival_at']==value['estimated_arrival_at']
        old=dict(value);old['timestamp']=timestamp(value['timestamp']);old['requested_at']=timestamp(value['requested_at']);old['expires_at']=timestamp(value['expires_at'])
        aged=reusable_prediction(old,clock[0]+timedelta(minutes=2),'rio_consulado','historical-unit',False)
        assert aged['waiting_time_min']==pytest.approx(value['waiting_time_min']-2-10/60)
        expired={**old,'estimated_arrival_at':clock[0]+timedelta(minutes=1),'waiting_time_min':1}
        assert reusable_prediction(expired,clock[0]+timedelta(minutes=2),'rio_consulado','historical-unit',False) is None
        clock[0]+=timedelta(minutes=120)
        payload={'query_id':'next-bus-unit-0001','actual_min':120,'wait_basis':'since_query'}
        response=api.post('/feedback',headers={'Authorization':'Bearer u'},json=payload)
        assert response.status_code==200 and not response.json()['outside_domain']
        assert store.memory.reports[('u','next-bus-unit-0001')]['arrival_eligible']


def test_monthly_adapter_requires_continuity_and_keeps_grid_separate(tmp_path):
    from Dataset.next_bus import prepare_maintenance
    from Dataset.preparar_modelo import read_rows
    from test_operations import camera_file
    camera_file(tmp_path)
    camera=read_rows(tmp_path/'camera.csv')
    with pytest.raises(ValueError,match='continuidad'):
        prepare_maintenance(camera,[], '2026-11-01T00:00:00Z')
    samples,quality=prepare_maintenance(camera,[], '2026-11-01T00:00:00Z',continuity_confirmed=True)
    assert quality['can_train'] and quality['schema_version']==NEXT_BUS_SCHEMA
    assert samples and all(r['record_type']=='derived_next_bus_query' for r in samples)
    assert all(r.get('record_type')!='derived_next_bus_query' for r in quality['next_bus_reports'])
    held=samples[0]['sample_id']
    again,_=prepare_maintenance(camera,[], '2026-11-01T00:00:00Z',continuity_confirmed=True,benchmark_ids=[held])
    assert held not in {r['sample_id'] for r in again}


def test_monthly_benchmark_includes_long_confirmed_waits_but_cannot_auto_change_goal():
    from Modelo.retrain_job import monthly_benchmark
    from Modelo.operacion.evaluation import compare
    from types import SimpleNamespace
    from app.model_inputs import calendar_context
    reports=[]
    for day in (1,2):
        for i in range(15):
            at=f'2026-11-{day:02d}T12:{i:02d}:00Z'
            reports.append(dict(uid=str(i),query_id=f'{day}-{i}',station_id='rio_consulado',query_at=at,
                received_at=f'2026-11-{day:02d}T14:{i:02d}:00Z',actual_min=120,simulation=False,
                wait_basis='since_query',context=calendar_context(at),prediction_target='next_bus_wait'))
    benchmark=monthly_benchmark(reports,'2026-11-03T00:00:00Z',schema_version=NEXT_BUS_SCHEMA)
    assert len(benchmark['cases'])==30 and benchmark['target_definition']=='next_bus_wait'
    new=SimpleNamespace(metadata={'target_definition':'next_bus_wait','schema_version':NEXT_BUS_SCHEMA})
    old=SimpleNamespace(metadata={'target_definition':'personal_wait'})
    with pytest.raises(ValueError,match='Cambio de objetivo'):compare(benchmark,new,old)
