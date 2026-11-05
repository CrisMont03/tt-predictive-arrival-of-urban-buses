"""Read-only acceptance check for an approved package against frozen validation vectors."""
import argparse,json,faulthandler,os,tempfile
faulthandler.enable()
faulthandler.dump_traceback_later(90,exit=True)
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
print("Cargando NumPy",file=sys.stderr,flush=True)
import numpy as np
from app.predictor import Predictor

p=argparse.ArgumentParser();p.add_argument('--model-dir',type=Path)
p.add_argument('--acceptance-file',type=Path);a=p.parse_args()
if a.model_dir is None:
    from app.artifacts import configured_model_directory
    a.model_dir,_=configured_model_directory()
if a.acceptance_file is None:
    from Modelo.retrain_job import pinned_object
    from google.cloud import storage
    reference=dict(uri=os.environ['ARRIBO_ACCEPTANCE_URI'],generation=os.environ['ARRIBO_ACCEPTANCE_GENERATION'],sha256=os.environ['ARRIBO_ACCEPTANCE_SHA256'])
    a.acceptance_file=Path(tempfile.mkdtemp())/'acceptance.npz'
    a.acceptance_file.write_bytes(pinned_object(reference,storage.Client(),limit=20_000_000))
print("Cargando LSTM",file=sys.stderr,flush=True)
predictor=Predictor(a.model_dir);errors=[];count=0
with np.load(a.acceptance_file) as archive:
    for name in [f'r{f}d{d}' for f in (25,50,100) for d in (0,5,15)]+['cold']:
        print('Verificando '+name,file=sys.stderr,flush=True)
        inputs={key:archive[f'{name}_{key}'] for key in ('sequence','step_mask')}
        expected=archive[f'{name}_expected']
        raw=predictor.model(inputs,training=False).numpy().reshape(-1)
        actual=np.maximum(0,raw) if predictor.metadata.get('target_definition')=='next_bus_wait' else np.clip(raw,0,45)
        if (not len(expected) or len(actual)!=len(expected) or not np.isfinite(actual).all()
                or inputs['sequence'].shape!=(len(expected),predictor.metadata['window'],27)
                or inputs['step_mask'].shape!=(len(expected),predictor.metadata['window'])
                or (actual>predictor.max_wait).any()):raise ValueError('Cobertura incompleta')
        error=float(np.max(abs(actual-expected)));errors.append(error);count+=len(expected)
if max(errors)>.0001:raise ValueError('El contenedor alteró las predicciones fuera de tolerancia')
faulthandler.cancel_dump_traceback_later()
result=dict(checked=count,max_absolute_difference=max(errors),approved=predictor.metadata['approved'],
    eligible=predictor.metadata['eligible'],schema=predictor.metadata['schema_version'],model_sha256=predictor.metadata['model_sha256'],
    review_kind='compatibility_only',independent_quality_evaluation=False)
if os.getenv('ARRIBO_REVIEW_RESULT_URI'):
    from app.artifacts import gcs_parts,json_bytes
    from google.cloud import storage
    from google.api_core.exceptions import PreconditionFailed
    bucket,name=gcs_parts(os.environ['ARRIBO_REVIEW_RESULT_URI']);blob=storage.Client().bucket(bucket).blob(name)
    try:blob.upload_from_string(json_bytes(result),if_generation_match=0,content_type='application/json')
    except PreconditionFailed:
        blob.reload()
        if blob.download_as_bytes(if_generation_match=blob.generation)!=json_bytes(result):raise
print(json.dumps(result))
