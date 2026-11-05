"""Local next-bus baseline, bounded search and retrospective evaluation. Never deploys."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
import shutil
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
os.environ.setdefault('TF_NUM_INTEROP_THREADS','2');os.environ.setdefault('TF_NUM_INTRAOP_THREADS','2')
from app.model_inputs import (NEXT_BUS_SCHEMA,NEXT_BUS_MAX_WAIT,FallbackAccumulator,contract,online_inputs,
    fallback_value,timestamp,fit_scaler,transform)
from Dataset.next_bus import load_csvs,weather_cache,queries_from_events,valid_camera_reports,sha,day_of
from Modelo.entrenar import build_lstm,metrics,write_json

PROFILES=[(f'r{int(f*100)}d{d}',f,d) for f in (.25,.5,1.) for d in (0,5,15)]+[('cold',0.,0)]


from Modelo.operacion.next_bus_training import freeze, SequenceBuilder, prepare, materialize, metadata, detailed, arrays, train_one


def baseline(prepared,output):
    import numpy as np
    prepared,output=Path(prepared),Path(output);output.mkdir(parents=True,exist_ok=False)
    prep=json.loads((prepared/'preparation.json').read_text());rows=[json.loads(l) for l in (prepared/'queries.jsonl').open()]
    val=[r for r in rows if r['split']=='validation']
    values=np.asarray([fallback_value(prep['fallback'],r['station_id'],r['event_at'],max_wait=NEXT_BUS_MAX_WAIT) for r in val])
    radius=float(np.quantile(abs(values-np.asarray([r['target'] for r in val])),.9,method='higher'))
    m=metadata(prep,rows,'next-bus-historical-v1','historical',radius)
    freeze(output/'historical.json',dict(fallback=prep['fallback'],radius=radius))
    m.update(historical_sha256=sha(output/'historical.json'),metrics={'all':detailed(val,values,radius)})
    freeze(output/'metadata.json',m);return m


def evaluate_frozen(prepared,candidates,output,legacy):
    import numpy as np
    from app.predictor import Predictor
    prepared=Path(prepared);rows=[json.loads(l) for l in (prepared/'queries.jsonl').open()]
    results=[]
    from app.model_inputs import calendar_context
    for split in ('validation','retrospective'):
        cases=[r for r in rows if r['split']==split]
        with np.load(prepared/(split+'.npz')) as archive:
            for directory in candidates:
                p=Predictor(directory,require_approved=False)
                for profile in ('r50d5','cold'):
                    if p.metadata['predictor_kind']=='historical':
                        values=np.asarray([fallback_value(p.metadata['fallback'],r['station_id'],r['event_at'],max_wait=NEXT_BUS_MAX_WAIT) for r in cases])
                    else:
                        raw=p.model(arrays(archive,profile),training=False).numpy().reshape(-1);values=np.maximum(0,raw)
                        if (values>NEXT_BUS_MAX_WAIT).any():raise ValueError('Modelo fuera de dominio')
                    results.append(dict(model=p.metadata['model_version'],split=split,scenario=profile,
                        **detailed(cases,values,p.metadata['radius'])))
                    with (Path(output)/(p.metadata['model_version']+'-'+split+'-'+profile+'.jsonl')).open('x') as f:
                        for row,v in zip(cases,values):f.write(json.dumps(dict(sample_id=row['sample_id'],target=row['target'],predicted_min=float(v)))+'\n')
        if legacy:
            p=Predictor(legacy);values=[]
            for r in cases:values.append(p.predict(r['context'],[],now=r['event_at'])[0])
            results.append(dict(model='published-personal-lstm-diagnostic',split=split,scenario='cold',
                **detailed(cases,np.asarray(values),p.metadata['radius']),domain_limit=45,
                targets_above_domain=sum(r['target']>45 for r in cases)))
    freeze(Path(output)/'comparison.json',results)
    import csv
    with (Path(output)/'comparison.csv').open('w') as f:
        fields=['model','split','scenario','count','missing','mae','rmse','r2','interval_coverage','interval_width','days','bus_intervals']
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(results)
    return results



from Modelo.operacion.next_bus_training import train_maintenance


def run(args):
    output=Path(args.output)
    if args.stage=='prepare':
        prepare(args.csv_root,args.weather,output/'preparation');return
    prep=output/'preparation'
    if args.stage=='search':
        baseline(prep,output/'historical')
        for opt in ('rmsprop','adam'):train_one(prep,output/f'lstm-{opt}-s2026050',opt,2026050)
        choices=[json.loads((output/f'lstm-{opt}-s2026050/metadata.json').read_text()) for opt in ('rmsprop','adam')]
        winner=min(choices,key=lambda m:(m['metrics']['r50d5']['mae'],m['metrics']['cold']['mae']))
        opt=winner['effective_parameters']['optimizer']
        for seed in (2026051,2026052):train_one(prep,output/f'lstm-{opt}-s{seed}',opt,seed)
        # The representative seed is fixed in advance, not selected using retrospective results.
        freeze(output/'selection.json',dict(optimizer=opt,representative_seed=2026050,selection='validation operational MAE then cold MAE',
            candidates=['historical',f'lstm-{opt}-s2026050',f'lstm-{opt}-s2026051',f'lstm-{opt}-s2026052'],approved=False))
    elif args.stage=='finalize':
        selection=json.loads((output/'selection.json').read_text());opt=selection['optimizer']
        final=output/'final';final.mkdir(exist_ok=False)
        baseline(prep,final/'historical')
        for seed in (2026050,2026051,2026052):
            m=train_one(prep,final/f'next-bus-lstm-{opt}-s{seed}',opt,seed)
            previous=json.loads((output/f'lstm-{opt}-s{seed}'/'metadata.json').read_text())
            if abs(m['metrics']['r50d5']['mae']-previous['metrics']['r50d5']['mae'])>.0001:
                raise ValueError('La consolidación cambió las predicciones: revisar antes de evaluar')
        freeze(final/'selection.json',{**selection,'configuration_frozen_before_retrospective':True,
            'candidates':['historical',*[f'next-bus-lstm-{opt}-s{s}' for s in (2026050,2026051,2026052)]]})
    elif args.stage=='evaluate':
        selected_root=output/'final' if (output/'final/selection.json').exists() else output
        selection=json.loads((selected_root/'selection.json').read_text())
        report=selected_root/'evaluation';report.mkdir(exist_ok=False)
        evaluate_frozen(prep,[selected_root/name for name in selection['candidates']],report,args.legacy)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=['prepare','search','finalize','evaluate'],required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--csv-root',type=Path,default=ROOT/'Procesamiento_de_videos/output/dataset')
    p.add_argument('--weather',type=Path,default=ROOT/'Modelo/output/reales-20251031-002/dataset_final.csv')
    p.add_argument('--legacy',type=Path,default=ROOT/'Modelo/output/section-h-20261104/package')
    run(p.parse_args())
