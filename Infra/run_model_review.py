"""Run native Linux compatibility review in an isolated Cloud Run Job; never route API traffic."""
import argparse,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--reference',type=Path,required=True);p.add_argument('--image',required=True)
p.add_argument('--execute',action='store_true');p.add_argument('--confirm-dev-project');a=p.parse_args()
r=json.loads(a.reference.read_text());model=r['model'];inputs=r['acceptance']
if '@sha256:' not in a.image:raise ValueError('Fija imagen por digest')
env=dict(GOOGLE_CLOUD_PROJECT='arribo-68326',ARRIBO_MODEL_MANIFEST_URI=model['uri'],ARRIBO_MODEL_MANIFEST_GENERATION=str(model['generation']),
 ARRIBO_MODEL_MANIFEST_SHA256=model['sha256'],ARRIBO_ACCEPTANCE_URI=inputs['uri'],ARRIBO_ACCEPTANCE_GENERATION=str(inputs['generation']),
 ARRIBO_ACCEPTANCE_SHA256=inputs['sha256'],ARRIBO_REVIEW_RESULT_URI=r['result_uri'])
common=['--project','arribo-68326','--region','us-central1','--quiet']
commands=[['gcloud','run','jobs','deploy','arribo-model-review','--image',a.image,'--service-account','arribo-promotor@arribo-68326.iam.gserviceaccount.com',
 '--tasks','1','--parallelism','1','--max-retries','0','--task-timeout','300s','--memory','2Gi','--cpu','2','--command','python',
 '--args','Modelo/review_model_package.py','--set-env-vars',','.join(k+'='+v for k,v in env.items()),*common],
 ['gcloud','run','jobs','execute','arribo-model-review','--wait',*common]]
if a.execute and a.confirm_dev_project!='arribo-68326':raise ValueError('Confirma el proyecto')
for command in commands:
 print(json.dumps(command),flush=True)
 if a.execute:subprocess.run(command,check=True)
