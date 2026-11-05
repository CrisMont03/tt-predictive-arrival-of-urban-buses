"""Explicit operator workflow for the authorized next-bus replacement; dry by default."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,required=True)
    p.add_argument('--action',choices=['register','stage','promote','rollback','resume'],required=True)
    p.add_argument('--execute',action='store_true');p.add_argument('--confirm-dev-project')
    a=p.parse_args();work=a.work.resolve();config=json.loads((work/'infra-config.json').read_text())
    grant=json.loads((work/'package/metadata.json').read_text())['manual_release_exception']
    cmd=[sys.executable,str(ROOT/'Modelo/promote_job.py'),'--execute','--confirm-dev-project','arribo-68326','--gcloud-session']
    if a.action=='register':
        cmd+=['--register-only','--run-id','release-next-bus-20261105','--manual-release',
              '--candidate',str(ROOT/'Modelo/output/next-bus-window-search-20261105-001/runs/next-bus-n30-u32-rmsprop-lr0.001-s2026050'),
              '--preparation',str(ROOT/'Modelo/output/next-bus-window-search-20261105-001/preparations/n30'),
              '--authorization',str(work/'package/metadata.json'),'--approval-operator',grant['operator'],'--approval-reason',grant['reason']]
    elif a.action in ('stage','promote'):
        cmd+=['--run-id','release-next-bus-20261105','--manual-release']
        if a.action=='stage':cmd+=['--stage-only']
    elif a.action=='rollback':cmd+=['--rollback']
    else:cmd+=['--resume-promotions','--approval-operator',grant['operator'],
               '--approval-reason','Recuperación de la revisión anterior comprobada; restaurar publicación manual autorizada.']
    if not a.execute:
        print(json.dumps(dict(command=cmd,writes=False)));return
    if a.confirm_dev_project!='arribo-68326' or config['project']!='arribo-68326':raise ValueError('Confirma el proyecto')
    env={**os.environ,'GOOGLE_CLOUD_PROJECT':'arribo-68326','ARRIBO_REGION':config['region'],
         'ARRIBO_SERVICE':config['service'],'ARRIBO_CODE_REVISION':config['code_revision'],
         'ARRIBO_REGISTRY_URI':'gs://arribo-68326-ops/maintenance',
         'ARRIBO_CANDIDATES_URI':'gs://arribo-68326-candidates/candidates',
         'ARRIBO_RELEASES_URI':'gs://arribo-68326-models/releases',
         'ARRIBO_API_IMAGE':config['api_image'],'ARRIBO_AUTO_PROMOTION':'false'}
    result=subprocess.run(cmd,env=env,text=True,capture_output=True)
    if result.returncode:
        print(result.stderr[-4000:],file=sys.stderr);raise RuntimeError('Falló acción '+a.action)
    value=json.loads(result.stdout)
    (work/(a.action+'-result.json')).write_text(json.dumps(value,indent=2));print(json.dumps(value))


if __name__=='__main__':main()
