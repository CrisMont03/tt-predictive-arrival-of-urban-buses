"""Operational feedback LSTM training. No experimental runners or holdout scoring."""
import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'Backend')]
from app.model_inputs import FEEDBACK_SCHEMA, FallbackAccumulator, contract, fit_scaler, online_inputs, timestamp, transform, is_synthetic_source
from Modelo.entrenar import build_lstm, load_preparation, metrics, sha256, write_json

PROFILES = [(f'r{int(f*100)}d{d}', f, d) for f in (.25, .5, 1) for d in (0, 5, 15)] + [('cold', 0, 0)]
DEFAULTS = dict(window=10, units=16, dense_units=16, optimizer='rmsprop', learning_rate=.0003,
                batch_size=32, epochs=50, patience=6, seed=2026050, shuffle=False)


def validate_spec(spec):
    if spec != DEFAULTS:
        raise ValueError('Especificación explícita incompatible con la receta operativa vigente')
    return dict(spec)


class Histories:
    def __init__(self, parts, fallback, window=10):
        self.window = window
        self.groups = defaultdict(list)
        self.starts = {}
        self.fallback = {}
        rows = [r for part in parts.values() for r in part]
        for row in rows:
            group = tuple(row['continuity_group'])
            start = timestamp(row.get('coverage_start', row['event_at']))
            self.starts[group] = min(self.starts.get(group, start), start)
            if not row['loss_weight']:
                continue
            query = timestamp(row['event_at'])
            camera = row.get('source') == 'camera'
            received = timestamp(row.get('available_at') or row['bus_arrival'])
            if camera:
                received = max(received, query + timedelta(minutes=row['target']))
            self.groups[group].append(dict(uid=('camera-' + row['sample_id']) if camera else row['uid'],
                query_id=row['sample_id'] if camera else row['query_id'], station_id=row['station_id'],
                query_at=query, received_at=received, actual_min=row['target'], context=row['context'],
                wait_basis='since_query' if camera else row.get('wait_basis', 'unspecified'),
                simulation=False, camera_proxy=camera))
        completed = sorted((r for r in parts['train'] if r['loss_weight']),
                           key=lambda r:(timestamp(r.get('available_at') or r['bus_arrival']),r['sample_id']))
        prefix = FallbackAccumulator();position = 0
        for row in sorted(rows, key=lambda r:(timestamp(r['event_at']),r['sample_id'])):
            now = timestamp(row['event_at'])
            while position < len(completed) and timestamp(completed[position].get('available_at') or completed[position]['bus_arrival']) < now:
                r = completed[position];prefix.add(r['station_id'],r['event_at'],r['target']);position += 1
            self.fallback[row['sample_id']] = deepcopy({} if row.get('boundary_excluded') else prefix.table if row['split']=='train' else fallback)

    def inputs(self, row, fraction, delay):
        reports = []
        for r in self.groups[tuple(row['continuity_group'])]:
            if not fraction:
                continue
            if r['camera_proxy']:
                retained = int(hashlib.sha256(f"2026054:{r['query_id']}".encode()).hexdigest(),16)/2**256 < fraction
                if not retained:
                    continue
                r = {**r,'received_at':r['received_at']+timedelta(minutes=delay)}
            reports.append(r)
        return online_inputs(row['context'],self.window,now=row['event_at'],station_id=row['station_id'],
            observations=reports,fallback=self.fallback[row['sample_id']],
            start_at=self.starts[tuple(row['continuity_group'])],schema_version=FEEDBACK_SCHEMA)


def train(data, output, version, spec):
    started = time.monotonic()
    spec = validate_spec(spec);data,output = Path(data),Path(output)
    if output.exists():
        raise ValueError('La salida debe ser nueva')
    quality, parts = load_preparation(data)
    if quality['schema_version'] != FEEDBACK_SCHEMA or quality['window'] != spec['window']:
        raise ValueError('Contrato o ventana incompatibles')
    if any(is_synthetic_source(r) or r.get('simulation') for part in parts.values() for r in part):
        raise ValueError('El mantenimiento operativo exige observaciones reales')
    import numpy as np
    os.environ.setdefault('TF_NUM_INTEROP_THREADS','2');os.environ.setdefault('TF_NUM_INTRAOP_THREADS','2')
    import tensorflow as tf
    tf.keras.utils.set_random_seed(spec['seed']);tf.config.experimental.enable_op_determinism()
    scaler = fit_scaler(parts['train']);builder = Histories(parts,quality['fallback'],spec['window'])
    selected = {k:[r for r in values if r['loss_weight']] for k,values in parts.items()}
    def arrays(rows,fraction,delay):
        values = [builder.inputs(r,fraction,delay) for r in rows]
        return dict(sequence=np.asarray([transform(x,scaler) for x in values],dtype='float32'),
                    step_mask=np.asarray([[bool(v[0]) for v in x['step_mask']] for x in values],dtype='bool'))
    train_sets = [arrays(selected['train'],f,d) for _,f,d in PROFILES]
    x = {k:np.concatenate([p[k] for p in train_sets]) for k in train_sets[0]}
    y = np.tile(np.asarray([r['target'] for r in selected['train']],dtype='float32'),len(PROFILES))
    weights = np.tile(np.asarray([r['loss_weight']/len(PROFILES) for r in selected['train']],dtype='float32'),len(PROFILES))
    valid = arrays(selected['validation'],.5,5)
    expected = np.asarray([r['target'] for r in selected['validation']],dtype='float32')
    options=tf.data.Options();options.threading.private_threadpool_size=1
    batches=tf.data.Dataset.from_tensor_slices((x,y.reshape(-1,1),weights)).batch(32).with_options(options)
    validation=tf.data.Dataset.from_tensor_slices((valid,expected.reshape(-1,1))).batch(32).with_options(options)
    model=build_lstm(spec['window'],spec['units'],spec['learning_rate'])
    model.compile(optimizer=tf.keras.optimizers.RMSprop(learning_rate=spec['learning_rate']),loss='mse')
    history=model.fit(batches,validation_data=validation,epochs=spec['epochs'],shuffle=False,
        callbacks=[tf.keras.callbacks.EarlyStopping(patience=6,restore_best_weights=True)],verbose=2)
    predict=lambda a:np.clip(model(a,training=False).numpy().reshape(-1),0,45)
    radius=float(np.quantile(abs(expected-predict(valid)),.9,method='higher'))
    scores={};records=[]
    for name,f,d in PROFILES:
        values=predict(arrays(selected['validation'],f,d));scores[name]=metrics(selected['validation'],values,radius)
        for r,value in zip(selected['validation'],values):
            records.append(dict(sample_id=r['sample_id'],scenario=name,target=r['target'],predicted_min=float(value),
                                interval=[max(0,float(value)-radius),float(value)+radius]))
    operational=scores['r50d5'];cold=scores['cold']
    eligible=not quality.get('simulation') and all(r['missing']==0 and r['mae']<5 for r in (operational,cold))
    output.mkdir(parents=True);model.save(output/'model.keras')
    restored=tf.keras.models.load_model(output/'model.keras',compile=False,safe_mode=True)
    assert np.allclose(model(valid,training=False).numpy(),restored(valid,training=False).numpy(),atol=1e-5,rtol=1e-5)
    metadata=dict(schema_version=FEEDBACK_SCHEMA,contract=contract(spec['window'],FEEDBACK_SCHEMA),window=spec['window'],
        model_version=version,model_sha256=sha256(output/'model.keras'),scaler=scaler,fallback=quality['fallback'],radius=radius,
        serving_bus_context='feedback-derived-arrivals-v1',simulation=False,approved=False,eligible=bool(eligible),
        eligibility_policy='operational-mae-v1',collection_goal_met=quality['meets_collection_goal'],
        effective_parameters=spec,training_profiles=PROFILES,metrics=scores,dataset_sha256=sha256(data/'sequences.jsonl'),
        training_sample_ids=[r['sample_id'] for r in selected['train']],calibration_sample_ids=[r['sample_id'] for r in selected['validation']],
        training_cutoff=max(timestamp(r['available_at']) for r in selected['train']).isoformat(),
        calibration_cutoff=max(timestamp(r['available_at']) for r in selected['validation']).isoformat(),
        dependencies={name:importlib.metadata.version(name) for name in ('numpy','tensorflow','keras','holidays')},
        code_hashes={str(p.relative_to(ROOT)):sha256(p) for p in [Path(__file__),ROOT/'Backend/app/model_inputs.py',ROOT/'Backend/app/feedback_arrivals.py']},
        quality_sha256=sha256(data/'quality.json'),duration_seconds=time.monotonic()-started,
        weight_policy=dict(camera=1,feedback=.5,scenario_divisor=10),evaluation_partitions=['validation'],
        roundtrip_verified=True,eligibility_note='La promoción requiere además benchmark independiente y mejora frente al vigente')
    write_json(output/'metadata.json',metadata)
    write_json(output/'history.json',{k:[float(v) for v in a] for k,a in history.history.items()})
    with (output/'validation_predictions.jsonl').open('x') as f:
        for row in records:f.write(json.dumps(row)+'\n')
    return metadata


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--version',required=True);p.add_argument('--spec-json',required=True);a=p.parse_args()
    train(a.data,a.output,a.version,json.loads(a.spec_json))
