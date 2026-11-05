"""Create an authorized copy and acceptance vectors without changing weights or GCP."""
import argparse
import json
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
from app.artifacts import digest, immutable_json
from app.manual_release import MODEL_SHA, issue
from app.predictor import Predictor
from Modelo.operacion.next_bus_training import PROFILES, arrays


def prepare(candidate, preparation, output, previous, operator, reason):
    import numpy as np
    candidate,preparation,output=map(Path,(candidate,preparation,output))
    original=(candidate/'metadata.json').read_bytes();m=json.loads(original)
    if digest((candidate/'model.keras').read_bytes())!=MODEL_SHA or m['model_sha256']!=MODEL_SHA:
        raise ValueError('Pesos incompatibles')
    if digest((preparation/'preparation.json').read_bytes())!=m['preparation_sha256']:
        raise ValueError('Preparación incompatible')
    if output.exists():raise ValueError('Usa una carpeta nueva')
    grant=issue(m,operator=operator,reason=reason,source_sha256=digest(original),previous=previous)
    output.mkdir(parents=True)
    shutil.copyfile(candidate/'model.keras',output/'model.keras')
    immutable_json(output/'metadata.json',{**m,'approved':True,'manual_release_exception':grant})
    p=Predictor(output);payload={};count=0
    with np.load(preparation/'validation.npz') as archive:
        for name,_,_ in PROFILES:
            x=arrays(archive,name)
            values=np.maximum(0,p.model(x,training=False).numpy().reshape(-1))
            if len(values)!=1676 or not np.isfinite(values).all():raise ValueError('Cobertura inválida')
            records=candidate/f'validation-{name}.jsonl'
            if records.exists():
                expected=np.asarray([json.loads(line)['predicted_min'] for line in records.open()])
                np.testing.assert_allclose(values,expected,atol=1e-4,rtol=0)
            for key,value in x.items():payload[f'{name}_{key}']=value
            payload[f'{name}_expected']=values;count+=len(values)
    np.savez_compressed(output.parent/'acceptance.npz',**payload)
    assert (candidate/'metadata.json').read_bytes()==original
    immutable_json(output.parent/'local-review.json',dict(model_sha256=MODEL_SHA,checked=count,
        original_unchanged=True,weights_unchanged=True,eligible=False,approved_copy=True,
        independent_quality_evaluation=False,cloud_deployed=False))
    print('Approved local copy; acceptance cases:',count)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('candidate','preparation','output','previous'):p.add_argument('--'+n,type=Path,required=True)
    for n in ('operator','reason'):p.add_argument('--'+n,required=True)
    a=p.parse_args();prepare(a.candidate,a.preparation,a.output,json.loads(a.previous.read_text()),a.operator,a.reason)
