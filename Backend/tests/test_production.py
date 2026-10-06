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
