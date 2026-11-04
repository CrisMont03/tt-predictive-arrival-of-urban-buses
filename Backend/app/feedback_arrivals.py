"""Causal, partial arrivals inferred from explicitly confirmed wait reports."""
from datetime import datetime, timedelta
from statistics import median

from .model_inputs import MAX_WAIT, finite, is_synthetic_source, timestamp

SCHEMA = 'arribo-sequences-feedback-v1'
POLICY = 'feedback-derived-arrivals-v1'
GROUP_SECONDS = 60


def arrival_fields(query_at, actual_min, received_at, wait_basis='unspecified', *, simulation=False):
    result = dict(wait_basis=wait_basis, derived_bus_arrival_at=None,
                  arrival_eligible=False, arrival_policy=POLICY)
    if wait_basis != 'since_query':
        return {**result, 'arrival_exclusion': 'unspecified_wait_start'}
    if simulation or not 0 <= actual_min <= MAX_WAIT:
        return {**result, 'arrival_exclusion': 'simulation_or_domain'}
    arrival = timestamp(query_at) + timedelta(minutes=actual_min)
    if arrival > timestamp(received_at):
        return {**result, 'arrival_exclusion': 'arrival_after_receipt'}
    return {**result, 'derived_bus_arrival_at': arrival, 'arrival_eligible': True,
            'arrival_exclusion': None}


def grouped_arrivals(reports, at, station_id, *, start_at=None, allow_offline_synthetic=False):
    """Recompute each snapshot: late evidence cannot rewrite earlier snapshots."""
    at = timestamp(at)
    lower = at - timedelta(hours=2)
    if start_at is not None:
        lower = max(lower, timestamp(start_at))
    candidates = {}
    for r in reports or []:
        try:
            if (r.get('simulation') or r.get('outside_domain') or r.get('station_id') != station_id
                    or (is_synthetic_source(r) and not allow_offline_synthetic)):
                continue
            query, received = timestamp(r['query_at']), timestamp(r['received_at'])
            actual = finite(r['actual_min'])
            if actual is None or not query <= received < at:
                continue
            fields = arrival_fields(query, actual, received, r.get('wait_basis', 'unspecified'))
            if not fields['arrival_eligible']:
                continue
            arrival = fields['derived_bus_arrival_at']
            if not lower <= arrival < at:
                continue
            identity = (str(r['uid']), str(r['query_id']))
            if not all(identity):
                continue
            item = dict(identity=identity, arrival=arrival, received=received)
            if identity not in candidates or received < candidates[identity]['received']:
                candidates[identity] = item
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    ordered = sorted(candidates.values(), key=lambda r: (r['arrival'], r['identity']))
    groups = []
    for item in ordered:
        if not groups or (item['arrival'] - groups[-1][0]['arrival']).total_seconds() > GROUP_SECONDS:
            groups.append([])
        groups[-1].append(item)
    result = []
    for group in groups:
        users = {}
        for item in group:
            uid = item['identity'][0]
            if uid not in users or (item['received'], item['identity']) < (users[uid]['received'], users[uid]['identity']):
                users[uid] = item
        evidence = sorted(users.values(), key=lambda r: r['identity'])
        value = median(r['arrival'].timestamp() for r in evidence)
        result.append(dict(arrival_at=datetime.fromtimestamp(value, tz=at.tzinfo),
                           evidence=[dict(uid=r['identity'][0], query_id=r['identity'][1],
                                          received_at=r['received'].isoformat(),
                                          derived_at=r['arrival'].isoformat()) for r in evidence]))
    return result


def apply_arrival_context(inputs, reports, now, station_id, *, start_at=None, allow_offline_synthetic=False):
    from .model_inputs import bus_context_at
    evidence = []
    for i, step in enumerate(inputs['provenance']):
        if step['kind'] not in ('current', 'observed'):
            continue
        at = timestamp(step['query_at'])
        # No reports received after the prediction cutoff, even when constructing historic steps.
        cutoff_reports = [r for r in reports or [] if _before(r, now)]
        groups = grouped_arrivals(cutoff_reports, at, station_id, start_at=start_at,
                                  allow_offline_synthetic=allow_offline_synthetic)
        values = bus_context_at([g['arrival_at'] for g in groups], at)
        inputs['bus_context'][i] = [v if v is not None else 0 for v in values]
        inputs['bus_mask'][i] = [int(v is not None) for v in values]
        evidence.append(dict(step=i, groups=[dict(arrival_at=g['arrival_at'].isoformat(),
                                                  evidence=g['evidence']) for g in groups[-4:]]))
    inputs['arrival_evidence'] = evidence
    return inputs


def _before(report, now):
    try:
        return timestamp(report['received_at']) < timestamp(now)
    except (KeyError, TypeError, ValueError):
        return False
