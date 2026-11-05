"""Explicit one-model exception; never an eligibility policy for monthly candidates."""
from datetime import datetime, timezone
from .artifacts import digest, json_bytes
from .model_inputs import FEEDBACK_SCHEMA, contract

MODEL = "lstm-n10-u16-rmsprop-lr0.0003-s2026050"
MODEL_SHA = "ddfc92900ac3ee91ffcee887376f53f02917add5199c5da500034ca1f4fc6e7b"


def identity(metadata):
    p = metadata.get("effective_parameters", {})
    return (metadata.get("model_version") == MODEL and metadata.get("model_sha256") == MODEL_SHA
            and metadata.get("simulation") is False and metadata.get("eligible") is False
            and metadata.get("schema_version") == FEEDBACK_SCHEMA and metadata.get("window") == 10
            and metadata.get("contract") == contract(10, FEEDBACK_SCHEMA)
            and p.get("units") == 16 and p.get("optimizer") == "rmsprop"
            and p.get("learning_rate") == .0003)


def issue(metadata, *, operator, reason, source_sha256):
    if not identity(metadata) or metadata.get("approved") is not False or not operator.strip() or not reason.strip():
        raise ValueError("La excepción inicial no corresponde al candidato autorizado")
    return dict(format="arribo-initial-exception-v1", project="arribo-68326", model_version=MODEL,
                model_sha256=MODEL_SHA, metadata_sha256=digest(json_bytes(metadata)),
                original_metadata_sha256=source_sha256, operator=operator.strip(), reason=reason.strip(),
                at=datetime.now(timezone.utc).isoformat(), independent_evaluation_pending=True)


def valid(metadata):
    grant = metadata.get("initial_release_exception", {})
    if not isinstance(grant, dict):
        return False
    original = {k: v for k, v in metadata.items() if k != "initial_release_exception"}
    original["approved"] = False
    return bool(identity(metadata) and metadata.get("approved") is True
                and grant.get("format") == "arribo-initial-exception-v1"
                and grant.get("project") == "arribo-68326"
                and grant.get("model_version") == MODEL and grant.get("model_sha256") == MODEL_SHA
                and grant.get("operator") and grant.get("reason") and grant.get("at")
                and grant.get("independent_evaluation_pending") is True
                and grant.get("metadata_sha256") == digest(json_bytes(original)))


def evaluation(metadata, grant):
    if not valid({**metadata, "approved": True, "initial_release_exception": grant}):
        raise ValueError("Autorización inicial inválida o metadatos alterados")
    return dict(simulation=False, candidate_version=MODEL, incumbent_version=None, publishable=False,
                decision="manual_initial_approval", initial_release_exception=grant,
                independent_evaluation_pending=True)
