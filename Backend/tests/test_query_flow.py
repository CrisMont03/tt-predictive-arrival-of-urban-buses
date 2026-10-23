"""Section D: deterministic server cache, snapshots and feedback; no remote writes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from firebase_admin import auth

from app.integration_predictor import IntegrationPredictor, integration_context
from app.main import create_app
from app.model_inputs import select_observations
from app.store import MemoryStore
from run_development import arguments


class Adapter:
    """Unit-only stand-in; production normally uses FirestoreStore."""
    def __init__(self):
        self.memory = MemoryStore()

    def __getattr__(self, name):
        return getattr(self.memory, name)


@pytest.fixture
def flow(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("ARRIBO_MODEL_DIR", raising=False)
    monkeypatch.setattr(auth, "verify_id_token", lambda token, **kwargs: {"uid": token})
    clock = [datetime(2026, 10, 20, 14, tzinfo=timezone.utc)]
    store, predictor = Adapter(), IntegrationPredictor()
    api = TestClient(create_app("production", store, predictor, allow_integration=True,
                               clock=lambda: clock[0], context_provider=integration_context),
                     raise_server_exceptions=False)
    yield api, store, predictor, clock
    api.close()


def query(api, qid="section-d-0001", uid="alice"):
    return api.post("/predict", headers={"Authorization": f"Bearer {uid}"},
                    json={"station_id": "rio_consulado", "request_id": qid})


def feedback(api, qid="section-d-0001", minutes=15, uid="alice"):
    return api.post("/feedback", headers={"Authorization": f"Bearer {uid}"},
                    json={"query_id": qid, "actual_min": minutes})


def test_hit_without_inference_records_distinct_personal_queries(flow):
    api, store, predictor, clock = flow
    first = query(api).json()
    clock[0] += timedelta(seconds=1)
    second = query(api, "section-d-0002", "bob").json()
    assert first["source"] == "mock" and second["source"] == "cache"
    assert first["simulation"] and second["simulation"]
    assert predictor.calls == 1
    assert second["timestamp"] == first["timestamp"]
    assert second["expires_at"] == first["expires_at"]
    assert second["requested_at"] != first["requested_at"]
    assert second["query_id"] == "section-d-0002"
    assert set(store.queries) == {("alice", "section-d-0001"), ("bob", "section-d-0002")}


def test_expired_retry_returns_original_then_new_consultation_gets_new_prediction(flow):
    api, store, predictor, clock = flow
    original = query(api).json()
    clock[0] += timedelta(minutes=6)
    assert query(api).json() == original
    assert predictor.calls == 1
    fresh = query(api, "section-d-0002").json()
    assert fresh["source"] == "mock" and fresh["timestamp"] != original["timestamp"]
    assert predictor.calls == 2
    assert len(store.queries) == 2


@pytest.mark.parametrize("field,value", [("model_version", "integration-next"), ("model_sha256", "new-weights"),
                                          ("radius", 6), ("context_profile", "real")])
def test_changed_artifact_never_reuses_previous_cache(flow, field, value):
    api, store, predictor, clock = flow
    query(api)
    predictor.metadata[field] = value
    assert query(api, "section-d-0002").json()["source"] == "mock"
    assert predictor.calls == 2


def test_scaler_change_invalidates_cache_even_with_same_model_version(flow):
    api, store, predictor, clock = flow
    query(api)
    predictor.metadata["scaler"]["temporal"]["mean"][5] += 1
    query(api, "section-d-0002")
    assert predictor.calls == 2


@pytest.mark.parametrize("change", [
    {"simulation": False}, {"station_id": "wrong"}, {"waiting_time_min": float("nan")},
    {"context_sources": {}}, {"expires_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
])
def test_incompatible_or_expired_cache_is_a_miss(flow, change):
    api, store, predictor, clock = flow
    query(api)
    for cached in store.cache.values():
        cached.update(change)
    assert query(api, "section-d-0002").json()["source"] == "mock"
    assert predictor.calls == 2


def test_source_lifetime_caps_ttl_and_context_change_invalidates(monkeypatch):
    monkeypatch.setattr(auth, "verify_id_token", lambda *a, **k: {"uid": "alice"})
    now = [datetime(2026, 10, 20, 14, tzinfo=timezone.utc)]
    temperature = [20]
    async def provider():
        return dict(temp_c=temperature[0], _sources={"weather": {
            "status": "available", "valid_until": (now[0] + timedelta(seconds=30)).isoformat()}})
    predictor = IntegrationPredictor()
    with TestClient(create_app("production", Adapter(), predictor, allow_integration=True,
                               clock=lambda: now[0], context_provider=provider)) as api:
        first = query(api).json()
        assert datetime.fromisoformat(first["expires_at"]) == now[0] + timedelta(seconds=30)
        assert query(api, "section-d-0002").json()["source"] == "cache"
        now[0] += timedelta(seconds=31)
        assert query(api, "section-d-0003").json()["source"] == "mock"
        temperature[0] = 25
        assert query(api, "section-d-0004").json()["source"] == "mock"
    assert predictor.calls == 3


def test_simulated_feedback_is_associated_idempotent_and_not_an_observation(flow):
    api, store, predictor, clock = flow
    original = query(api).json()
    clock[0] += timedelta(seconds=1)
    assert feedback(api).json()["simulation"] is True
    report = deepcopy(store.reports[("alice", original["query_id"])])
    assert feedback(api).status_code == 200
    assert store.reports[("alice", original["query_id"])] == report
    assert store.get_query("alice", original["query_id"])["actual_min"] == 15
    assert feedback(api, minutes=16).status_code == 409
    assert feedback(api, uid="bob").status_code == 404
    assert store.recent_observations("rio_consulado", clock[0] - timedelta(hours=2)) == []
    assert query(api, "section-d-0002").json()["source"] == "cache"
    assert predictor.calls == 1
    assert original["context"] == report["context"]


def test_mechanism_a_and_cache_use_only_available_admissible_reports(flow):
    api, store, predictor, clock = flow
    query(api)
    # Synthetic observational fixtures exist only in this isolated unit store.
    base = dict(query_id="observed-unit-only", uid="bob", station_id="rio_consulado",
                actual_min=18, context={"temp_c": 19}, simulation=False, outside_domain=False,
                query_at=clock[0] - timedelta(minutes=30), received_at=clock[0] - timedelta(seconds=1))
    invalid = [{**base, "query_id": "future-unit", "received_at": clock[0] + timedelta(minutes=1)},
               {**base, "query_id": "demo-unit", "simulation": True},
               {**base, "query_id": "long-unit", "actual_min": 60, "outside_domain": True},
               {**base, "query_id": "station-unit", "station_id": "other"}]
    for item in [base, *invalid]:
        store.reports[("bob", item["query_id"])] = item
    result = query(api, "section-d-0002").json()
    assert result["source"] == "mock" and result["simulation"]
    assert result["waiting_time_min"] == 18
    assert predictor.calls == 2
    assert predictor.last_inputs["past_wait"][-2] == [18]
    assert predictor.last_inputs["past_wait_mask"][-1] == [0]
    assert predictor.last_inputs["bus_mask"] == [[0] * 4] * 10
    assert [p["query_id"] for p in predictor.last_inputs["provenance"] if p["kind"] == "observed"] == [base["query_id"]]
    assert query(api, "section-d-0003").json()["source"] == "cache"


@pytest.mark.parametrize("committed", [False, True])
def test_retry_recovers_from_partial_history_failure(flow, monkeypatch, committed):
    api, store, predictor, clock = flow
    original_create = store.create_query
    attempts = [0]
    def interrupted(uid, qid, value):
        attempts[0] += 1
        if attempts[0] == 1:
            if committed:
                original_create(uid, qid, value)
            raise OSError("unit-only lost response")
        return original_create(uid, qid, value)
    monkeypatch.setattr(store, "create_query", interrupted)
    assert query(api).status_code == 500
    retried = query(api)
    assert retried.status_code == 200
    assert retried.json()["query_id"] == "section-d-0001"
    assert len(store.queries) == 1 and predictor.calls == 1
    assert feedback(api).status_code == 200


def test_concurrent_identical_requests_have_one_canonical_history(flow):
    api, store, predictor, clock = flow
    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: query(api), range(4)))
    assert all(response.status_code == 200 for response in responses)
    assert len(store.queries) == 1
    assert all(response.json() == responses[0].json() for response in responses)


def test_test_predictor_requires_opt_in_and_health_never_claims_real_model(monkeypatch, flow):
    api, store, predictor, clock = flow
    with pytest.raises(ValueError, match="explícita"):
        create_app("production", store, predictor)
    health = api.get("/health").json()
    assert health["predictor_kind"] == "integration" and health["predictor_ready"]
    assert health["simulation"] and not health["model_ready"]
    monkeypatch.setenv("K_SERVICE", "unit-cloud-run")
    with pytest.raises(ValueError, match="Cloud Run"):
        create_app("production", store, predictor, allow_integration=True)


def test_runner_preserves_default_and_guards_development_project(monkeypatch):
    for key in ("ARRIBO_MODEL_DIR", "K_SERVICE", "FIRESTORE_EMULATOR_HOST", "FIREBASE_AUTH_EMULATOR_HOST"):
        monkeypatch.delenv(key, raising=False)
    base = ["--project", "arribo-68326", "--confirm-dev-project", "arribo-68326"]
    assert arguments(base).predictor == "unavailable"
    assert arguments(base + ["--predictor", "integration"]).predictor == "integration"
    with pytest.raises(SystemExit):
        arguments(["--project", "another", "--confirm-dev-project", "another", "--predictor", "integration"])
    with pytest.raises(SystemExit):
        arguments(base + ["--context", "fixture"])


def test_no_feedback_builds_fallback_without_current_target(flow):
    api, store, predictor, clock = flow
    result = query(api)
    assert result.status_code == 200
    assert predictor.last_inputs["wait_origin"].count([0, 1, 0]) == 9
    assert predictor.last_inputs["past_wait_mask"][-1] == [0]
    assert predictor.last_inputs["wait_origin"][-1] == [0, 0, 1]


@pytest.mark.parametrize("committed", [False, True])
def test_cache_write_failure_never_creates_unconfirmed_history(flow, monkeypatch, committed):
    api, store, predictor, clock = flow
    original_set = store.set_cache
    attempts = [0]
    def interrupted(key, value):
        attempts[0] += 1
        if attempts[0] == 1:
            if committed:
                original_set(key, value)
            raise OSError("unit-only lost cache acknowledgement")
        original_set(key, value)
    monkeypatch.setattr(store, "set_cache", interrupted)
    assert query(api).status_code == 500
    assert store.queries == {}
    assert query(api).status_code == 200
    assert len(store.queries) == 1
    assert predictor.calls == (1 if committed else 2)


def test_context_expired_during_inference_is_not_returned_as_fresh(monkeypatch):
    monkeypatch.setattr(auth, "verify_id_token", lambda *a, **k: {"uid": "alice"})
    now = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)
    async def provider():
        return {"temp_c": 20, "_sources": {"weather": {"valid_until": (now - timedelta(seconds=1)).isoformat()}}}
    store = Adapter()
    with TestClient(create_app("production", store, IntegrationPredictor(), allow_integration=True,
                               clock=lambda: now, context_provider=provider)) as api:
        result = query(api)
        assert result.status_code == 503 and result.json()["code"] == "CONTEXT_EXPIRED"
    assert store.queries == {} and store.cache == {}
