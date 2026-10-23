"""Server-only cache contract. TTL cleanup is never a freshness check."""
import hashlib
import json
from datetime import timedelta

from .context import cached_sources_valid
from .contracts import PredictResponse
from .model_inputs import contract, timestamp


def cache_key(station_id, context, prepared, observations, metadata, simulation):
    payload = {
        "station_id": station_id, "simulation": simulation,
        "contract": contract(metadata.get("window", 10)),
        "artifact": {key: metadata.get(key) for key in
                     ("model_version", "model_sha256", "scaler", "fallback", "radius", "context_profile")},
        "context": context,
        "inputs": {key: value for key, value in prepared.items() if key != "provenance"},
        "history": [(item.get("uid"), item["query_id"], item["actual_min"],
                     timestamp(item["received_at"]).isoformat()) for item in observations],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def cache_expiration(generated_at, sources):
    expires = generated_at + timedelta(minutes=5)
    for source in (sources or {}).values():
        if source.get("valid_until"):
            expires = min(expires, timestamp(source["valid_until"]))
    return expires


def reusable_prediction(value, now, station_id, model_version, simulation):
    if not value:
        return None
    try:
        prediction = PredictResponse.model_validate(value)
        if (prediction.station_id != station_id or prediction.model_version != model_version
                or prediction.simulation != simulation or prediction.timestamp > now
                or not prediction.timestamp < prediction.expires_at <= prediction.timestamp + timedelta(minutes=5)
                or prediction.expires_at <= now
                or not cached_sources_valid(prediction.context_sources, now)):
            return None
        return prediction.model_dump()
    except (ValueError, TypeError):
        return None
