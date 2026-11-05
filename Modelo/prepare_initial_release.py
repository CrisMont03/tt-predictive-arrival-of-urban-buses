"""Create a local, explicitly authorized copy of the selected first model; no GCP writes."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
from app.artifacts import digest,immutable_json,read_json
from app.initial_approval import issue,MODEL_SHA
from app.predictor import Predictor


def prepare(candidate,preparation,output,operator,reason):
    candidate,preparation,output=Path(candidate),Path(preparation),Path(output)
    if output.exists():
        raise ValueError('Usa una carpeta nueva; no sobrescribir una publicación')
    original=(candidate/'metadata.json').read_bytes();metadata=json.loads(original)
    weights=(candidate/'model.keras').read_bytes()
    if digest(weights)!=MODEL_SHA or metadata['model_sha256']!=MODEL_SHA:
        raise ValueError('No corresponde al archivo autorizado')
    prep=read_json(preparation/'preparation.json')
    if digest((preparation/'preparation.json').read_bytes())!=metadata['preparation_sha256']:
        raise ValueError('La preparación no corresponde a los metadatos')
    metadata['dataset_sha256']=prep['source_sha256']
    grant=issue(metadata,operator=operator,reason=reason,source_sha256=digest(original))
    output.mkdir(parents=True)
    shutil.copyfile(candidate/'model.keras',output/'model.keras')
    immutable_json(output/'metadata.json',{**metadata,'approved':True,'initial_release_exception':grant})
    predictor=Predictor(output)
    import numpy as np
    payload={};errors=[]
    records=[json.loads(l) for l in (candidate/'validation_predictions.jsonl').read_text().splitlines()]
    with np.load(preparation/'preparation.npz') as archive:
        for name in [f'r{f}d{d}' for f in (25,50,100) for d in (0,5,15)]+['cold']:
            x={k:archive[f'val_{name}_{k}'] for k in ('sequence','step_mask')}
            expected=np.asarray([r['predicted_min'] for r in records if r['scenario']==name])
            values=np.clip(predictor.model(x,training=False).numpy().reshape(-1),0,45)
            error=float(np.max(abs(values-expected)));errors.append(error)
            if len(expected)!=163 or error>1e-4:
                raise ValueError('La copia autorizada alteró las predicciones')
            for k,v in x.items():payload[f'{name}_{k}']=v
            payload[f'{name}_expected']=expected
    np.savez_compressed(output.parent/'acceptance.npz',**payload)
    immutable_json(output.parent/'local-review.json',dict(model_version=metadata['model_version'],model_sha256=MODEL_SHA,
        original_metadata_sha256=digest(original),approved_copy=True,original_unchanged=(candidate/'metadata.json').read_bytes()==original,
        validation_predictions_checked=1630,max_absolute_difference=max(errors),tolerance_minutes=.0001,
        independent_evaluation_pending=True,cloud_deployed=False))
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('candidate','preparation','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--operator',required=True);p.add_argument('--reason',required=True);a=p.parse_args()
    print(prepare(a.candidate,a.preparation,a.output,a.operator,a.reason))
