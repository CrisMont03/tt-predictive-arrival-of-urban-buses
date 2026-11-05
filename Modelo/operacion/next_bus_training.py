"""Shared operational next-bus preparation and training; no search or deployment."""
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

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'Backend')]
os.environ.setdefault('TF_NUM_INTEROP_THREADS','2');os.environ.setdefault('TF_NUM_INTRAOP_THREADS','2')
from app.model_inputs import (NEXT_BUS_SCHEMA,NEXT_BUS_MAX_WAIT,FallbackAccumulator,contract,online_inputs,
    fallback_value,timestamp,fit_scaler,transform)
from Dataset.next_bus import load_csvs,weather_cache,queries_from_events,valid_camera_reports,sha,day_of
from Modelo.entrenar import build_lstm,metrics,write_json

PROFILES=[(f'r{int(f*100)}d{d}',f,d) for f in (.25,.5,1.) for d in (0,5,15)]+[('cold',0.,0)]


def freeze(path,value):
    Path(path).write_text(json.dumps(value,indent=2,allow_nan=False,default=str))


class SequenceBuilder:
    def __init__(self,rows,reports,window=10):
        self.window=window;self.rows=rows;self.profiles={};self.fallbacks={}
        completed=sorted([r for r in rows if r['split']=='train'],key=lambda r:(r['available_at'],r['sample_id']))
        prefix=FallbackAccumulator(NEXT_BUS_MAX_WAIT);i=0;snapshots={}
        for row in sorted(rows,key=lambda r:(r['event_at'],r['sample_id'])):
            while i<len(completed) and timestamp(completed[i]['available_at'])<timestamp(row['event_at']):
                r=completed[i];prefix.add(r['station_id'],r['event_at'],r['target']);i+=1
            if i not in snapshots:snapshots[i]=deepcopy(prefix.table)
            self.fallbacks[row['sample_id']]=snapshots[i]
        frozen=FallbackAccumulator(NEXT_BUS_MAX_WAIT)
        for r in completed:frozen.add(r['station_id'],r['event_at'],r['target'])
        self.fallback=frozen.table
        for name,f,delay in PROFILES:
            days=defaultdict(list)
            for r in reports:
                if not f:continue
                camera=r.get('source')=='camera'
                if camera and int(hashlib.sha256(('2026054:'+r['query_id']).encode()).hexdigest(),16)/2**256 >= f:continue
                received=timestamp(r['received_at'])+timedelta(minutes=delay if camera else 0)
                days[r['day']].append({**r,'received_at':received})
            self.profiles[name]={day:sorted(rs,key=lambda r:r['received_at']) for day,rs in days.items()}

    def inputs(self,row,profile):
        now=timestamp(row['event_at']);rs=self.profiles[profile].get(row['day'],[])
        times=[r['received_at'] for r in rs]
        recent=rs[bisect_left(times,now-timedelta(hours=2)):bisect_left(times,now)]
        table=self.fallbacks[row['sample_id']] if row['split']=='train' else self.fallback
        return online_inputs(row['context'],self.window,now=now,station_id=row['station_id'],observations=recent,
            fallback=table,schema_version=NEXT_BUS_SCHEMA)


def prepare(root,weather_path,output):
    import numpy as np
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    events,reports,sources=load_csvs(root);weather=weather_cache(weather_path)
    rows,coverage,excluded=queries_from_events(events,weather)
    reports,report_exclusions=valid_camera_reports(reports,events,weather)
    for name,items in [('events',events),('reports',reports)]:
        with (output/(name+'.jsonl')).open('x') as f:
            for r in items:f.write(json.dumps(r,default=str)+'\n')
    freeze(output/'coverage.json',coverage)
    freeze(output/'weather-used.json',dict(source=str(weather_path),sha256=sha(weather_path) if weather_path else None,
        values=[dict(day=d,hour=h,**v) for (d,h),v in sorted(weather.items())]))
    provenance=dict(sources=sources,events=len(events),personal_report_proxies=len(reports),exclusions=excluded,
        report_exclusions=report_exclusions,continuity=coverage,
        scope='Real CSV bus events only; derived minutes are not observed passenger records',
        evaluation_policy='days12-15 retrospective, already examined; new prospective evaluation required')
    return materialize(rows,reports,output,provenance),rows


def materialize(rows,reports,output,provenance,window=10):
    """Shared numeric preparation; no fitting on validation or retrospective rows."""
    import numpy as np
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    builder=SequenceBuilder(rows,reports,window)
    training=[{**r,'inputs':builder.inputs(r,'r50d5')} for r in rows if r['split']=='train']
    scaler=fit_scaler(training);del training
    with (output/'queries.jsonl').open('x') as f:
        for r in rows:f.write(json.dumps(r,default=str)+'\n')
    preparation=dict(**provenance,schema_version=NEXT_BUS_SCHEMA,target_definition='next_bus_wait',
        contract=contract(window,NEXT_BUS_SCHEMA),window=window,scaler=scaler,fallback=builder.fallback,
        source_sha256=sha(output/'queries.jsonl'),counts=dict(Counter(r['split'] for r in rows)),
        unique_intervals=len({r.get('interval_id',r['sample_id']) for r in rows}),maximum_target=max(r['target'] for r in rows))
    freeze(output/'preparation.json',preparation)
    for split in sorted({r['split'] for r in rows}):
        selected=[r for r in rows if r['split']==split];packed={}
        for name,_,_ in PROFILES:
            values=[builder.inputs(r,name) for r in selected]
            packed[name+'_sequence']=np.asarray([transform(v,scaler) for v in values],dtype='float32')
            packed[name+'_step_mask']=np.asarray([[bool(x[0]) for x in v['step_mask']] for v in values],dtype='bool')
            print('Prepared',split,name,len(values),flush=True)
        packed['target']=np.asarray([r['target'] for r in selected],dtype='float32')
        packed['weight']=np.asarray([r['loss_weight'] for r in selected],dtype='float32')
        np.savez_compressed(output/(split+'.npz'),**packed)
    return preparation


def metadata(prep,rows,version,kind,radius):
    window=prep["window"]
    train=[r for r in rows if r['split']=='train'];val=[r for r in rows if r['split']=='validation']
    return dict(schema_version=NEXT_BUS_SCHEMA,target_definition='next_bus_wait',predictor_kind=kind,model_version=version,
        station_id='rio_consulado',contract=contract(window,NEXT_BUS_SCHEMA),window=window,scaler=prep['scaler'],fallback=prep['fallback'],
        radius=float(radius),simulation=False,approved=False,eligible=False,max_wait=NEXT_BUS_MAX_WAIT,
        serving_bus_context='feedback-derived-arrivals-v1',dataset_sha256=prep['source_sha256'],
        training_sample_ids=[r['sample_id'] for r in train],calibration_sample_ids=[r['sample_id'] for r in val],
        training_cutoff=max(r['available_at'] for r in train),calibration_cutoff=max(r['available_at'] for r in val),
        evaluation_policy=prep['evaluation_policy'],source_counts=prep['counts'],
        code_hashes={name:sha(ROOT/name) for name in ('Dataset/next_bus.py','Modelo/operacion/next_bus_training.py','Backend/app/model_inputs.py','Backend/app/feedback_arrivals.py')})


def detailed(rows,values,radius):
    import numpy as np
    result=metrics(rows,values,radius)
    result['hours']={str(h):metrics([r for r in rows if r['context']['hour']==h],np.asarray([v for r,v in zip(rows,values) if r['context']['hour']==h]),radius) for h in sorted({r['context']['hour'] for r in rows})}
    bins=[('0-15',0,15),('15-45',15,45),('45-90',45,90),('90+',90,float('inf'))]
    result['wait_bands']={key:metrics([r for r in rows if lo<=r['target']<hi],np.asarray([v for r,v in zip(rows,values) if lo<=r['target']<hi]),radius) for key,lo,hi in bins}
    result['days']=len({r['day'] for r in rows});result['bus_intervals']=len({r['interval_id'] for r in rows})
    return result


def arrays(archive,profile):
    return {k:archive[profile+'_'+k] for k in ('sequence','step_mask')}


def train_one(prepared,output,optimizer,seed,epochs=50,*,units=16,learning_rate=None):
    import numpy as np
    import tensorflow as tf
    started=time.monotonic();prepared=Path(prepared);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    prep=json.loads((prepared/'preparation.json').read_text());rows=[json.loads(line) for line in (prepared/'queries.jsonl').open()]
    valrows=[r for r in rows if r['split']=='validation']
    tf.keras.backend.clear_session();tf.keras.utils.set_random_seed(seed);tf.config.experimental.enable_op_determinism()
    with np.load(prepared/'train.npz') as train,np.load(prepared/'validation.npz') as val:
        x={key:np.concatenate([train[name+'_'+key] for name,_,_ in PROFILES]) for key in ('sequence','step_mask')}
        y=np.tile(train['target'],len(PROFILES));weights=np.tile(train['weight'] if 'weight' in train else np.ones(len(train['target']),dtype='float32'),len(PROFILES))/len(PROFILES)
        options=tf.data.Options();options.threading.private_threadpool_size=1
        batches=tf.data.Dataset.from_tensor_slices((x,y.reshape(-1,1),weights)).batch(32).with_options(options)
        vx=arrays(val,'r50d5');vy=val['target']
        validation=tf.data.Dataset.from_tensor_slices((vx,vy.reshape(-1,1))).batch(32).with_options(options)
        rate=learning_rate if learning_rate is not None else (.0003 if optimizer=='rmsprop' else .001)
        model=build_lstm(prep["window"],units,rate)
        opt=tf.keras.optimizers.RMSprop(rate) if optimizer=='rmsprop' else tf.keras.optimizers.Adam(rate)
        model.compile(optimizer=opt,loss='mse')
        history=model.fit(batches,validation_data=validation,epochs=epochs,shuffle=False,
            callbacks=[tf.keras.callbacks.EarlyStopping(monitor='val_loss',patience=6,restore_best_weights=True)],verbose=2)
        def predict(profile):
            raw=model(arrays(val,profile),training=False).numpy().reshape(-1)
            if not np.isfinite(raw).all() or (raw>NEXT_BUS_MAX_WAIT).any():raise ValueError('Predicción fuera de dominio')
            return np.maximum(0,raw)
        radius=float(np.quantile(abs(vy-predict('r50d5')),.9,method='higher'))
        scores={name:detailed(valrows,predict(name),radius) for name,_,_ in PROFILES}
        model.save(output/'model.keras')
        restored=tf.keras.models.load_model(output/'model.keras',compile=False,safe_mode=True)
        assert np.allclose(restored(vx,training=False).numpy(),model(vx,training=False).numpy(),atol=1e-5)
        meta=metadata(prep,rows,output.name,'lstm',radius)
        meta.update(model_sha256=sha(output/'model.keras'),metrics=scores,
            effective_parameters=dict(window=prep["window"],units=units,dense_units=16,optimizer=optimizer,learning_rate=rate,seed=seed,
                batch_size=32,max_epochs=epochs,patience=6,shuffle=False),
            dependencies={n:importlib.metadata.version(n) for n in ('tensorflow','keras','numpy')},
            duration_seconds=time.monotonic()-started,epochs_run=len(history.history['loss']),
            best_epoch=int(np.argmin(history.history['val_loss']))+1,roundtrip_verified=True)
        meta['preparation_sha256']=sha(prepared/'preparation.json')
        meta['array_hashes']={name:sha(prepared/(name+'.npz')) for name in ('train','validation')}
        for relative in meta['code_hashes']:
            target=output/'source-snapshot'/relative;target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/relative,target)
        freeze(output/'metadata.json',meta);freeze(output/'history.json',history.history)
        return meta



MONTHLY_DEFAULTS=dict(window=30,units=32,dense_units=16,optimizer='rmsprop',learning_rate=.001,
    batch_size=32,epochs=50,patience=6,seed=2026050,shuffle=False)


def validate_monthly_spec(value):
    if value != MONTHLY_DEFAULTS:
        raise ValueError('Receta mensual próxima llegada incompatible')
    return dict(value)

def train_maintenance(data,output,version,training):
    """Local/cloud adapter for a separately configured next-bus Job; never approves."""
    import tempfile
    training=validate_monthly_spec(training)
    data=Path(data);quality=json.loads((data/'quality.json').read_text())
    rows=[json.loads(line) for line in (data/'sequences.jsonl').open()]
    workspace=Path(tempfile.mkdtemp(prefix='next-bus-maintenance-'))
    materialize(rows,quality['next_bus_reports'],workspace,dict(evaluation_policy=quality['evaluation_policy'],continuity=quality['continuity']),window=training['window'])
    result=train_one(workspace,output,training['optimizer'],training.get('seed',2026050),epochs=training['epochs'],units=training['units'],learning_rate=training['learning_rate'])
    path=Path(output)/'metadata.json';result['model_version']=version;result['eligible']=all(result['metrics'][k]['missing']==0 and result['metrics'][k]['mae']<5 for k in ('r50d5','cold'));freeze(path,result)
    return result


