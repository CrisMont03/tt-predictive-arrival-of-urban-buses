"""Next-arrival labels from CSV bus events and explicitly confirmed daily continuity."""
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import timedelta
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'Backend')]
from app.model_inputs import calendar_context, timestamp, LOCAL_ZONE, NEXT_BUS_MAX_WAIT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def day_of(at):
    return timestamp(at).astimezone(LOCAL_ZONE).date().isoformat()


def split_day(day):
    return 'train' if day <= '2025-10-09' else 'validation' if day <= '2025-10-11' else 'retrospective'


def load_csvs(root):
    events = {}; reports = []; sources = []; seen_people = set()
    for day in range(1, 16):
        date = f'2025-10-{day:02d}'
        for name in ('dataset', 'datasets'):
            for path in sorted((Path(root)/date.replace('-', '_')/name).glob('*_v4_dataset.csv')):
                sources.append(dict(path=str(path.resolve()), sha256=sha(path)))
                with path.open(encoding='utf-8-sig', newline='') as f:
                    for line, row in enumerate(csv.DictReader(f), 2):
                        try:
                            arrival = timestamp(row['bus_arrival']); start = timestamp(row['arrival_user'])
                            sentinel = int(row['track_id_persona']) == -1
                            wait = float(row['waiting_time_min'])
                            if day_of(arrival) != date or start > arrival or wait < 0 or abs((arrival-start).total_seconds()/60-wait) > .1:
                                raise ValueError('Espera o jornada inconsistente')
                            if sentinel and (wait != 0 or start != arrival):
                                raise ValueError('Centinela inconsistente')
                            key = (date, arrival.isoformat())
                            event = events.setdefault(key, dict(day=date, arrival_at=arrival.isoformat(), sources=[], sentinel_rows=0))
                            event['sources'].append(dict(path=str(path.resolve()), line=line))
                            event['sentinel_rows'] += int(sentinel)
                            if not sentinel:
                                identity = hashlib.sha256(json.dumps([str(path.resolve()), line, row], sort_keys=True).encode()).hexdigest()
                                if identity in seen_people:
                                    raise ValueError('Persona duplicada')
                                seen_people.add(identity)
                                reports.append(dict(uid='camera-'+identity, query_id=identity, station_id='rio_consulado',
                                    query_at=start.isoformat(), received_at=arrival.isoformat(), actual_min=(arrival-start).total_seconds()/60,
                                    wait_basis='since_query', simulation=False, day=date, source='camera'))
                        except (KeyError, ValueError, TypeError) as e:
                            raise ValueError(f'{path}:{line}: {e}') from e
    if not sources:
        raise ValueError('No hay CSV diarios autorizados')
    return sorted(events.values(), key=lambda e:e['arrival_at']), reports, sources


def weather_cache(path):
    """Reuse only weather columns of an earlier enriched export, never its labels."""
    values = {}
    if path is None:
        return values
    with Path(path).open(encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            at = timestamp(r['arrival_user']).astimezone(LOCAL_ZONE)
            key = (at.date().isoformat(), at.hour)
            value = {k:float(r[k]) if r.get(k) not in (None,'') else None for k in ('precipitation_mm','temp_c','humidity')}
            if key in values and values[key] != value:
                raise ValueError(f'Clima inconsistente en {key}')
            values[key] = value
    return values


def queries_from_events(events, weather=None, *, partition=split_day):
    days = defaultdict(set)
    for event in events:
        days[event['day']].add(timestamp(event['arrival_at']))
    queries=[];coverage=[];counts=Counter()
    for day, arrivals in sorted(days.items()):
        buses = sorted(arrivals)
        if len(buses)<2:
            counts['days_without_complete_interval']+=1
            continue
        first,last = buses[0],buses[-1]
        coverage.append(dict(day=day,start_at=first.isoformat(),end_at=last.isoformat(),verified=True,
            evidence='User explicitly confirmed continuous and complete arrivals within each day, 2025-10-01..15; no cross-day continuity'))
        at=first.replace(second=0,microsecond=0)
        if at<first:at+=timedelta(minutes=1)
        while at<=last:
            index=bisect_left(buses,at);right=buses[index]
            left=buses[index-1] if index else first
            target=(right-at).total_seconds()/60
            if not 0<=target<=NEXT_BUS_MAX_WAIT:raise ValueError('Espera fuera de jornada')
            identity=hashlib.sha256(f'rio_consulado:{day}:{at.isoformat()}'.encode()).hexdigest()
            queries.append(dict(sample_id='next-bus-'+identity,station_id='rio_consulado',day=day,
                event_at=at.isoformat(),available_at=right.isoformat(),bus_arrival=right.isoformat(),target=target,
                interval_id=hashlib.sha256(f'{day}:{left.isoformat()}:{right.isoformat()}'.encode()).hexdigest(),
                split=partition(day),record_type='derived_next_bus_query',source='confirmed_camera_arrivals',
                loss_weight=1.,simulation=False,coverage_start=first.isoformat(),
                context={**calendar_context(at),**(weather or {}).get((day,at.astimezone(LOCAL_ZONE).hour),{})}))
            at+=timedelta(minutes=1)
    if not queries:raise ValueError('Faltan intervalos completos')
    return queries,coverage,dict(counts)


def valid_camera_reports(reports, events, weather=None):
    buses=defaultdict(list)
    for e in events:buses[e['day']].append(timestamp(e['arrival_at']))
    result=[];excluded=Counter()
    for r in reports:
        at=timestamp(r['query_at']);times=sorted(set(buses[r['day']]))
        i=bisect_left(times,at)
        if i==len(times) or times[i]!=timestamp(r['received_at']):
            excluded['assigned_bus_is_not_next_observed_bus']+=1
            continue
        result.append({**r,'context':{**calendar_context(at),**(weather or {}).get((r['day'],at.astimezone(LOCAL_ZONE).hour),{})}})
    return result,dict(excluded)


def confirmed_feedback(reports, cutoff, held_ids=()):
    """Monthly labels remain real reports, never a grid across partial bus reports."""
    result=[];excluded=Counter();seen=set();held=set(held_ids)
    for r in reports:
        try:
            if r.get('simulation') is not False or r.get('source')=='synthetic' or r.get('is_synthetic') or r.get('wait_basis')!='since_query':
                excluded['unconfirmed_or_simulated']+=1;continue
            at=timestamp(r['query_at']);received=timestamp(r['received_at']);wait=float(r['actual_min'])
            uid,qid=r['uid'],r['query_id'];identity='feedback-'+hashlib.sha256(json.dumps([uid,qid],sort_keys=True).encode()).hexdigest()
            if not uid or not qid or r['station_id']!='rio_consulado' or not 0<=wait<=NEXT_BUS_MAX_WAIT or not at+timedelta(minutes=wait)<=received<timestamp(cutoff):
                excluded['invalid_or_after_cutoff']+=1;continue
            if identity in held or (uid,qid) in seen:
                excluded['held_or_duplicate']+=1;continue
            seen.add((uid,qid))
            result.append(dict(sample_id=identity,station_id=r['station_id'],day=day_of(at),event_at=at.isoformat(),
                available_at=received.isoformat(),target=wait,context=r['context'],source='feedback',record_type='confirmed_next_bus_feedback',
                loss_weight=.5,simulation=False,query_id=qid,uid=uid,wait_basis='since_query'))
        except (KeyError, ValueError, TypeError, OverflowError):excluded['malformed']+=1
    return result,dict(excluded)


def prepare_maintenance(camera_rows,feedback,cutoff,*,benchmark_ids=(),continuity_confirmed=False):
    """Historical complete intervals plus confirmed feedback; never infer missing buses from reports."""
    if not continuity_confirmed:
        raise ValueError('El nuevo objetivo requiere confirmar continuidad del histórico')
    events={};proxies=[];weather={};cutoff=timestamp(cutoff)
    for r in camera_rows:
        arrival=timestamp(r['bus_arrival']);start=timestamp(r['arrival_user'])
        if arrival>=cutoff:continue
        day=day_of(arrival)
        events[(day,arrival.isoformat())]=dict(day=day,arrival_at=arrival.isoformat(),sources=[],sentinel_rows=int(r['record_type']=='bus_only'))
        at=start.astimezone(LOCAL_ZONE)
        weather[(day_of(start),at.hour)]={k:float(r[k]) if r.get(k) not in ('',None) else None for k in ('temp_c','humidity','precipitation_mm')}
        if r['record_type']=='user_wait':
            identity=r['sample_id']
            proxies.append(dict(uid='camera-'+identity,query_id=identity,day=day,station_id=r['station_id'],query_at=start.isoformat(),
                received_at=arrival.isoformat(),actual_min=(arrival-start).total_seconds()/60,wait_basis='since_query',simulation=False,source='camera'))
    grid,coverage,_=queries_from_events(list(events.values()),weather,partition=lambda day:'pending')
    grid=[r for r in grid if timestamp(r['available_at'])<cutoff and r['sample_id'] not in set(benchmark_ids)]
    people,exclusions=confirmed_feedback(feedback,cutoff,benchmark_ids)
    combined=grid+people
    days=sorted({day_of(r['available_at']) for r in combined})
    if len(days)<3:raise ValueError('Datos insuficientes: al menos tres jornadas para ajuste y calibración')
    edge=days[min(len(days)-1,max(1,int(len(days)*.8)))]
    filtered=[]
    for r in combined:
        r['split']='train' if day_of(r['available_at'])<edge else 'validation'
        if r['split']=='validation' and day_of(r['event_at'])<edge:
            exclusions['cross_partition_feedback']=exclusions.get('cross_partition_feedback',0)+1;continue
        r.setdefault('interval_id',r['sample_id']);filtered.append(r)
    proxies,proxy_exclusions=valid_camera_reports(proxies,list(events.values()),weather)
    for r in feedback:
        accepted,_=confirmed_feedback([r],cutoff,benchmark_ids)
        if accepted:
            proxies.append({**r,'day':day_of(r['query_at']),'source':'feedback'})
    # Receipts from a different partition never enter the earlier partition's histories.
    filtered.sort(key=lambda r:(r['event_at'],r['sample_id']))
    quality=dict(schema_version='arribo-next-bus-feedback-v1',window=10,can_train=all(any(r['split']==s for r in filtered) for s in ('train','validation')),
        simulation=False,continuity=coverage,next_bus_reports=proxies,exclusions={**exclusions,**proxy_exclusions},
        evaluation_policy='Monthly benchmark excluded from fit/calibration; original fixed holdouts must remain excluded in history source')
    return filtered,quality
