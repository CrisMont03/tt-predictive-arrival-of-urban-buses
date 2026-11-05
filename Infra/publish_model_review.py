"""Stage a private authorized package and validation vectors for native Linux review."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
from app.artifacts import digest,json_bytes,publish_package,read_json
from app.initial_approval import valid
from Infra.cloud_session import credentials

p=argparse.ArgumentParser();p.add_argument('--package',type=Path,required=True);p.add_argument('--acceptance',type=Path,required=True)
p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true');p.add_argument('--confirm-dev-project');a=p.parse_args()
meta=read_json(a.package/'metadata.json')
if not valid(meta):raise ValueError('El paquete no tiene la autorización inicial esperada')
if not a.execute:print('Inspección: no se publica nada');sys.exit(0)
if a.confirm_dev_project!='arribo-68326':raise ValueError('Confirma el proyecto')
from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
client=storage.Client(project='arribo-68326',credentials=credentials())
release='review-'+digest((a.package/'metadata.json').read_bytes())[:20]
model=publish_package(a.package,'gs://arribo-68326-candidates/reviews',release,client=client)
content=a.acceptance.read_bytes();name='reviews/inputs-'+digest(content)[:20]+'.npz'
blob=client.bucket('arribo-68326-candidates').blob(name)
try:blob.upload_from_string(content,if_generation_match=0)
except PreconditionFailed:
    blob.reload()
    if blob.download_as_bytes(if_generation_match=blob.generation)!=content:raise ValueError('Vectores modificados')
reference=dict(model=model,acceptance=dict(uri='gs://arribo-68326-candidates/'+name,generation=int(blob.generation),sha256=digest(content)),
    result_uri='gs://arribo-68326-ops/maintenance/reviews/'+release+'.json')
a.output.write_text(json.dumps(reference,indent=2));print(json.dumps(reference))
