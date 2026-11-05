"""Explicit authorization for one immutable next-bus replacement; never monthly eligibility."""
from datetime import datetime, timezone
from .artifacts import digest, json_bytes
from .model_inputs import NEXT_BUS_SCHEMA, contract

MODEL = 'next-bus-n30-u32-rmsprop-lr0.001-s2026050'
MODEL_SHA = 'ebd07c372ce5843cbcc1ec52222c92d3962764995f27478cbcd8cc57ec0dec2b'


def identity(metadata):
    p = metadata.get('effective_parameters', {})
    return (metadata.get('model_version') == MODEL and metadata.get('model_sha256') == MODEL_SHA
            and metadata.get('simulation') is False and metadata.get('eligible') is False
            and metadata.get('schema_version') == NEXT_BUS_SCHEMA
            and metadata.get('target_definition') == 'next_bus_wait' and metadata.get('window') == 30
            and metadata.get('contract') == contract(30, NEXT_BUS_SCHEMA)
            and p.get('units') == 32 and p.get('dense_units') == 16
            and p.get('optimizer') == 'rmsprop' and p.get('learning_rate') == .001)


def issue(metadata, *, operator, reason, source_sha256, previous):
    if (not identity(metadata) or metadata.get('approved') is not False or not operator.strip()
            or not reason.strip() or not previous.get('model_version') or not previous.get('package')
            or not previous.get('deployment', {}).get('revision')):
        raise ValueError('Autorización manual incompatible o sin versión anterior')
    return dict(format='arribo-manual-release-v1', project='arribo-68326', model_version=MODEL,
                model_sha256=MODEL_SHA, metadata_sha256=digest(json_bytes(metadata)),
                original_metadata_sha256=source_sha256, operator=operator.strip(), reason=reason.strip(),
                previous=previous, at=datetime.now(timezone.utc).isoformat(),
                independent_evaluation_pending=True, quality_thresholds_not_met=True)


def valid(metadata):
    grant=metadata.get('manual_release_exception', {})
    if not isinstance(grant, dict): return False
    original={k:v for k,v in metadata.items() if k != 'manual_release_exception'}
    original['approved']=False
    previous=grant.get('previous', {})
    return bool(identity(metadata) and metadata.get('approved') is True
        and grant.get('format') == 'arribo-manual-release-v1' and grant.get('project') == 'arribo-68326'
        and grant.get('model_version') == MODEL and grant.get('model_sha256') == MODEL_SHA
        and grant.get('operator') and grant.get('reason') and grant.get('at')
        and grant.get('independent_evaluation_pending') is True and grant.get('quality_thresholds_not_met') is True
        and isinstance(previous, dict) and previous.get('model_version') and previous.get('package')
        and previous.get('deployment', {}).get('revision')
        and grant.get('metadata_sha256') == digest(json_bytes(original)))


def evaluation(metadata, grant, incumbent):
    if not valid({**metadata, 'approved':True, 'manual_release_exception':grant}):
        raise ValueError('Autorización manual inválida')
    if incumbent != grant['previous']['model_version']:
        raise ValueError('El vigente no corresponde a la autorización')
    return dict(simulation=False, candidate_version=MODEL, incumbent_version=incumbent,
                publishable=False, decision='manual_replacement_approval', manual_release_exception=grant,
                independent_evaluation_pending=True, quality_thresholds_not_met=True)
