"""Publish immutable operational history/spec using the operator's gcloud session."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
from app.artifacts import digest,json_bytes
from Infra.cloud_session import credentials
from Modelo.operacion.training import DEFAULTS
from Modelo.operacion.next_bus_training import MONTHLY_DEFAULTS


def main():
    p=argparse.ArgumentParser();p.add_argument('--history',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--execute',action='store_true')
    p.add_argument('--confirm-dev-project');p.add_argument('--next-bus',action='store_true');a=p.parse_args()
    content=a.history.read_bytes()
    if a.next_bus:
        from Dataset.preparar_modelo import read_rows
        from app.model_inputs import is_synthetic_source
        rows=read_rows(a.history)
        if not rows or any(is_synthetic_source(r) or not '2025-10-01'<=r['date']<='2025-10-09' for r in rows):
            raise ValueError('El histórico próximo bus debe ser real y de los días 1–9')
    if not a.execute:
        print(json.dumps(dict(project='arribo-68326',history_sha256=digest(content),writes=False)));return
    if a.confirm_dev_project!='arribo-68326':raise ValueError('Confirma el proyecto')
    from google.cloud import storage
    from google.api_core.exceptions import PreconditionFailed
    bucket=storage.Client(project='arribo-68326',credentials=credentials()).bucket('arribo-68326-datasets')
    def put(name,content):
        blob=bucket.blob(name)
        try:blob.upload_from_string(content,if_generation_match=0)
        except PreconditionFailed:
            blob.reload()
            if blob.download_as_bytes(if_generation_match=blob.generation)!=content:raise ValueError('Objeto inmutable incompatible')
        return dict(uri=f'gs://{bucket.name}/{name}',generation=int(blob.generation),sha256=digest(content))
    history=put('history/operational-'+digest(content)[:20]+'.csv',content)
    spec=dict(schema_version='arribo-sequences-feedback-v1',simulation=False,window=10,epochs=50,
              training=DEFAULTS,minimum_benchmark=30,history=history,
              history_scope='Observaciones 2025-10-01 a 2025-10-11; se preservan días 12–15 fuera del ajuste')
    if a.next_bus:
        spec.update(schema_version='arribo-next-bus-feedback-v1',window=30,training=MONTHLY_DEFAULTS,
                    continuity_confirmed=True,history_scope='CSV reales 2025-10-01 a 2025-10-09; cobertura diaria confirmada; días 10–31 excluidos',
                    target_definition='next_bus_wait',auto_promotion=False)
    payload=json_bytes(spec);reference=put('specs/monthly-'+digest(payload)[:20]+'.json',payload)
    a.output.write_text(json.dumps(dict(training_spec=reference,history=history,spec=spec),indent=2))
    print(json.dumps(dict(training_spec=reference,history=history)))


if __name__=='__main__':main()
