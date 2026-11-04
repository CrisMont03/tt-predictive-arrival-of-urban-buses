from copy import deepcopy
from datetime import timedelta

import pytest

from app.feedback_arrivals import arrival_fields, grouped_arrivals
from app.model_inputs import FEEDBACK_SCHEMA, contract, online_inputs, timestamp
from app.prediction_cache import cache_key
from app.store import MemoryStore, FeedbackConflict

NOW = timestamp('2026-11-02T12:50:00Z')


def report(uid='alice', query_id='query-0001', query=30, wait=15, received=46):
    return dict(uid=uid, query_id=query_id, station_id='rio_consulado',
                query_at=NOW.replace(minute=query), actual_min=wait,
                received_at=NOW.replace(minute=received), wait_basis='since_query',
                context={}, simulation=False, outside_domain=False)


def inputs(reports, **kwargs):
    return online_inputs({}, 20, observations=reports, now=NOW, station_id='rio_consulado',
                         schema_version=FEEDBACK_SCHEMA, **kwargs)


def test_derivation_and_future_receipt():
    r = report()
    f = arrival_fields(r['query_at'], 15, r['received_at'], 'since_query')
    assert f['derived_bus_arrival_at'] == NOW.replace(minute=45)
    assert inputs([r])['bus_context'][-1] == [5, 0, 0, 0]
    assert inputs([r])['bus_mask'][-1] == [1, 0, 0, 0]
    r['received_at'] = NOW + timedelta(minutes=1)
    assert inputs([r])['bus_mask'][-1] == [0]*4


def test_legacy_and_bad_arrivals_stay_unknown():
    r = report(); r.pop('wait_basis')
    assert inputs([r])['past_wait'][-2] == [15]
    assert inputs([r])['bus_mask'][-1] == [0]*4
    assert arrival_fields(NOW, 15, NOW, 'since_query')['arrival_exclusion'] == 'arrival_after_receipt'
    assert not arrival_fields(NOW, 0, NOW, 'since_query', simulation=True)['arrival_eligible']


def test_probable_same_bus_distinct_users_and_no_chaining():
    reports = [report(), report('bob','query-0002',wait=15.8),
               report('alice','query-0003',wait=15.5), report('carol','query-0004',wait=16.6,received=48)]
    groups = grouped_arrivals(reports, NOW, 'rio_consulado')
    assert len(groups) == 2
    assert len(groups[0]['evidence']) == 2
    assert groups[0]['arrival_at'] == NOW.replace(minute=45, second=24)
    assert groups == grouped_arrivals(list(reversed(reports)), NOW, 'rio_consulado')


def test_partial_reports_do_not_invent_intervals_and_historical_snapshot_is_causal():
    rows = [report('a','query-0001',query=20,wait=5,received=26),
            report('b','query-0002',query=30,wait=15,received=46)]
    x = inputs(rows)
    assert x['bus_context'][-1] == [5,20,0,0]
    assert x['bus_mask'][-1] == [1,1,0,0]
    # At 12:30 the 12:25 report was already received, the 12:45 arrival was not.
    assert x['bus_context'][-2] == [5,0,0,0]
    future = deepcopy(rows[-1]); future['query_id']='future-001'; future['received_at']=NOW+timedelta(minutes=1)
    assert inputs(rows+[future]) == x


def test_fallbacks_padding_and_cache_evidence():
    assert inputs([])['step_mask'][:-1] == [[0]]*19
    m = dict(model_version='test', window=20, schema_version=FEEDBACK_SCHEMA)
    r = report()
    a = inputs([r]); b = inputs([{**r, 'wait_basis':'unspecified'}])
    assert cache_key('rio_consulado',{},a,[r],m,False) != cache_key('rio_consulado',{},b,[r],m,False)
    assert contract(20,FEEDBACK_SCHEMA)['input_width'] == 27


def test_retry_preserves_receipt_and_detects_basis_conflict():
    store = MemoryStore(); store.create_query('alice','query-0001',{})
    r = report(); first = store.save_feedback('alice','query-0001',r)
    retry = store.save_feedback('alice','query-0001',{**r,'received_at':NOW})
    assert retry['received_at'] == first['received_at']
    with pytest.raises(FeedbackConflict):
        store.save_feedback('alice','query-0001',{**r,'wait_basis':'unspecified'})


def test_arrivals_are_not_limited_to_nineteen_waits_and_ignore_wrong_station():
    old = report('old','query-old',query=5,wait=10,received=16)
    # More than 19 newer waits, but only the older report has a confirmed origin.
    many = [{**report(str(i),f'query-{i:04d}',query=20,wait=1,received=22),
             'wait_basis':'unspecified'} for i in range(25)]
    x = inputs([old]+many)
    assert sum(p['kind']=='observed' for p in x['provenance'])==19
    assert x['bus_context'][-1] == [35,0,0,0]
    assert inputs([{**old,'station_id':'other'}])['bus_mask'][-1]==[0]*4


def test_late_corroboration_cannot_change_a_historical_step():
    a=report('a','query-a001',query=5,wait=5,received=11)
    historic=report('c','query-c001',query=20,wait=1,received=22)
    later=report('b','query-b001',query=5,wait=5.5,received=30)
    before=inputs([a,historic]);after=inputs([a,historic,later])
    i=next(i for i,p in enumerate(after['provenance']) if p.get('query_id')=='query-c001')
    j=next(i for i,p in enumerate(before['provenance']) if p.get('query_id')=='query-c001')
    assert after['bus_context'][i]==before['bus_context'][j]
    assert after['bus_context'][-1]!=before['bus_context'][-1]


def test_new_request_contract_rejects_unknown_basis():
    from app.contracts import FeedbackRequest
    from pydantic import ValidationError
    assert FeedbackRequest(query_id='query-0001',actual_min=15).wait_basis=='unspecified'
    with pytest.raises(ValidationError):
        FeedbackRequest(query_id='query-0001',actual_min=15,wait_basis='guess')


def test_api_confirmed_feedback_feeds_prepared_prediction_and_cache(monkeypatch):
    from fastapi.testclient import TestClient
    from firebase_admin import auth
    from app.main import create_app
    from app.model_inputs import FallbackAccumulator
    from app.integration_predictor import integration_context
    clock=[NOW.replace(minute=30)]
    accumulator=FallbackAccumulator();accumulator.add('rio_consulado',NOW,10)
    class Adapter:
        def __init__(self):self.memory=MemoryStore()
        def __getattr__(self,key):return getattr(self.memory,key)
    class Recording:
        metadata=dict(schema_version=FEEDBACK_SCHEMA,window=20,model_version='unit-only',fallback=accumulator.table)
        calls=0
        def predict_prepared(self, prepared):
            self.calls+=1;self.last=prepared;return 10,(5,15)
    monkeypatch.delenv('ARRIBO_MODEL_DIR',raising=False)
    monkeypatch.setattr(auth,'verify_id_token',lambda token,**kw:{'uid':token})
    store=Adapter();predictor=Recording();headers={'Authorization':'Bearer alice'}
    with TestClient(create_app('production',store,predictor,clock=lambda:clock[0],context_provider=integration_context)) as api:
        assert api.post('/predict',headers=headers,json={'request_id':'first-0001'}).status_code==200
        clock[0]=NOW.replace(minute=46)
        body=dict(query_id='first-0001',actual_min=15,wait_basis='since_query')
        assert api.post('/feedback',headers=headers,json=body).status_code==200
        saved=store.reports[('alice','first-0001')]
        assert saved['derived_bus_arrival_at']==NOW.replace(minute=45)
        clock[0]=NOW
        a=api.post('/predict',headers=headers,json={'request_id':'next-00001'}).json()
        assert predictor.last['bus_context'][-1]==[5,0,0,0]
        b=api.post('/predict',headers=headers,json={'request_id':'next-00002'}).json()
        assert a['source']=='model' and b['source']=='cache' and predictor.calls==2
        assert api.post('/feedback',headers=headers,json=body).status_code==200
        assert store.reports[('alice','first-0001')]['received_at']==NOW.replace(minute=46)
        assert api.post('/feedback',headers=headers,json={**body,'wait_basis':'unspecified'}).status_code==409
