"""Authenticated release smoke using the previously authorized development account; no feedback labels."""
import argparse
import json
from pathlib import Path
import sys
import httpx
import firebase_admin
from firebase_admin import auth, credentials
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]
UID='0Qv94FxE5KZLJ0eRuB6WG4BV7Q93'


def run(url, model, prefix, key_file, output):
    account=credentials.Certificate(str(key_file))
    if account.project_id!='arribo-68326':raise ValueError('Proyecto de credenciales incorrecto')
    app=firebase_admin.initialize_app(account,options={'projectId':'arribo-68326'},name=prefix)
    auth.get_user(UID,app=app)
    custom=auth.create_custom_token(UID,app=app).decode()
    api_key=dotenv_values(ROOT/'App_Movil/.env')['EXPO_PUBLIC_FIREBASE_API_KEY']
    with httpx.Client(timeout=90) as client:
        response=client.post('https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken',
            params={'key':api_key},json={'token':custom,'returnSecureToken':True})
        if response.status_code!=200:raise RuntimeError('No fue posible autenticar la cuenta de pruebas: '+str(response.status_code))
        token=response.json()['idToken'];headers={'Authorization':'Bearer '+token}
        health=client.get(url+'/health');ready=client.get(url+'/ready')
        assert health.status_code==200 and ready.status_code==200
        assert ready.json()['model_version']==model
        def query(qid):
            r=client.post(url+'/predict',headers=headers,json={'station_id':'rio_consulado','request_id':qid})
            if r.status_code!=200:raise RuntimeError('Predict smoke failed: '+str(r.status_code))
            return r.json()
        q1=prefix+'-first';first=query(q1)
        assert first['model_version']==model and first['prediction_target']=='next_bus_wait'
        assert not first['simulation'] and first['estimated_arrival_at']
        second=query(prefix+'-second')
        # Retry only if the first two requests crossed the minute boundary.
        if second['source']!='cache':second=query(prefix+'-third')
        assert second['source']=='cache', 'No compatible live cache hit'
        replay=query(q1);assert replay==first
        # The app reads its history directly from Firestore under owner rules.
        history=client.get('https://firestore.googleapis.com/v1/projects/arribo-68326/databases/(default)/documents/query_history/'+UID+'/queries/'+q1,headers=headers)
        assert history.status_code==200
        assert history.json()['fields']['query_id']['stringValue']==q1
        unauthorized=client.post(url+'/predict',json={'station_id':'rio_consulado','request_id':prefix+'-no-auth'})
        assert unauthorized.status_code==401
        absent=client.post(url+'/feedback',headers=headers,json={'query_id':prefix+'-missing','actual_min':0,'wait_basis':'unspecified'})
        assert absent.status_code==404
        result=dict(url=url,ready=ready.json(),prediction_target=first['prediction_target'],
            first_source=first['source'],second_source=second['source'],waiting_time_min=first['waiting_time_min'],
            query_ids=[first['query_id'],second['query_id']],history_verified=True,idempotency_verified=True,
            unauthenticated_status=401,missing_feedback_status=404,feedback_written=False)
        output.write_text(json.dumps(result,indent=2));print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for n in ('url','model','prefix'):parser.add_argument('--'+n,required=True)
    parser.add_argument('--key-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    a=parser.parse_args();run(a.url,a.model,a.prefix,a.key_file,a.output)
