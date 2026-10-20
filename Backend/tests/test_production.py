from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from firebase_admin import auth

from app.main import create_app
from app.store import MemoryStore


class TestFirestoreAdapter:
    __test__ = False
    def __init__(self):
        self.memory = MemoryStore()

    def __getattr__(self, name):
        return getattr(self.memory, name)


def test_production_without_model_is_explicitly_unavailable(monkeypatch):
    monkeypatch.delenv("ARRIBO_MODEL_DIR", raising=False)
    monkeypatch.setattr(auth, "verify_id_token", lambda token, **kwargs: {"uid": "alice"})
    client = TestClient(create_app("production", TestFirestoreAdapter()))
    assert not client.get("/health").json()["model_ready"]
    result = client.post("/predict", headers={"Authorization": "Bearer valid-token"}, json={"request_id": "query-0001"})
    assert result.status_code == 503 and result.json()["code"] == "MODEL_NOT_READY"


def test_model_source_cache_snapshot_and_no_simulated_feedback(monkeypatch):
    class FakePredictor:
        metadata = {"model_version": "test-fixture"}
        def predict(self, context, observations):
            assert context["temp_c"] is None  # Failure is unknown, never zero.
            assert observations == []
            return 10, (5, 15)
    monkeypatch.setattr(auth, "verify_id_token", lambda token, **kwargs: {"uid": "alice"})
    monkeypatch.setattr("app.main.fetch_context", AsyncMock(return_value={"temp_c": None, "humidity": None, "precipitation_mm": None}))
    store = TestFirestoreAdapter()
    client = TestClient(create_app("production", store, FakePredictor()))
    headers = {"Authorization": "Bearer valid-token"}
    first = client.post("/predict", headers=headers, json={"request_id": "query-0001"}).json()
    second = client.post("/predict", headers=headers, json={"request_id": "query-0002"}).json()
    assert first["source"] == "model" and not first["simulation"]
    assert second["source"] == "cache" and not second["simulation"]
    assert len(store.queries) == 2
    feedback = client.post("/feedback", headers=headers, json={"query_id": "query-0001", "actual_min": 11}).json()
    assert not feedback["simulation"]


def test_feedback_preserves_provider_snapshot_without_fetching_report_time_context(monkeypatch):
    from copy import deepcopy

    class TestPredictor:
        metadata = {"model_version": "unit-test-only"}

        def predict(self, context, observations):
            assert context["traffic_density"] == 0.5
            assert context["env_alert_active"] == 1
            assert "_sources" not in context
            return 10, (5, 15)

    snapshot = {"traffic_density": 0.5, "env_alert_active": 1,
                "_sources": {"traffic": {"status": "available", "checked_at": "2026-10-17T19:00:00Z"},
                             "environmental": {"status": "available", "source_url": "https://www.gob.mx/comisionambiental/prensa/test"}}}
    fetch = AsyncMock(side_effect=lambda: deepcopy(snapshot))
    monkeypatch.setattr("app.main.fetch_context", fetch)
    monkeypatch.setattr(auth, "verify_id_token", lambda token, **kwargs: {"uid": "alice"})
    store = TestFirestoreAdapter()
    api = TestClient(create_app("production", store, TestPredictor()))
    headers = {"Authorization": "Bearer valid-test-token"}
    query = api.post("/predict", headers=headers, json={"request_id": "source-snapshot-01"}).json()
    assert query["context_sources"] == snapshot["_sources"]
    assert store.get_query("alice", query["query_id"])["context_sources"] == snapshot["_sources"]
    # Conditions can change before submission. Feedback must not re-fetch them.
    snapshot["traffic_density"] = 0.9
    snapshot["env_alert_active"] = 0
    for _ in range(2):
        response = api.post("/feedback", headers=headers, json={"query_id": query["query_id"], "actual_min": 15})
        assert response.status_code == 200
    report = store.reports[("alice", query["query_id"])]
    assert report["context"]["traffic_density"] == 0.5
    assert report["context"]["env_alert_active"] == 1
    assert report["context_sources"] == query["context_sources"]
    assert fetch.await_count == 1
