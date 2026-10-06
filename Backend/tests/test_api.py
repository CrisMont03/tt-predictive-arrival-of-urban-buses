from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.store import MemoryStore


def client():
    return TestClient(create_app("demo"))


def test_authentication_required():
    response = client().post("/predict", json={"request_id": "query-0001"})
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_cache_hit_records_each_user_query_and_request_retry_is_idempotent():
    api = client()
    headers = {"Authorization": "Bearer demo-alice"}
    first = api.post("/predict", headers=headers, json={"request_id": "query-0001"}).json()
    second = api.post("/predict", headers=headers, json={"request_id": "query-0002"}).json()
    assert first["source"] == "mock" and second["source"] == "cache"
    assert first["simulation"] and second["simulation"]
    assert len(api.app.state.store.queries) == 2
    first_query = api.app.state.store.get_query('demo-alice', 'query-0001')
    second_query = api.app.state.store.get_query('demo-alice', 'query-0002')
    assert second_query['timestamp'] == first_query['timestamp']
    assert second_query['requested_at'] >= first_query['requested_at']
    api.post("/predict", headers=headers, json={"request_id": "query-0002"})
    assert len(api.app.state.store.queries) == 2


def test_expired_cache_is_not_reused():
    api = client()
    headers = {"Authorization": "Bearer demo-alice"}
    api.post("/predict", headers=headers, json={"request_id": "query-0001"})
    for value in api.app.state.store.cache.values():
        value["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert api.post("/predict", headers=headers, json={"request_id": "query-0002"}).json()["source"] == "mock"


def test_feedback_ownership_snapshot_idempotency_and_long_waits():
    api = client()
    headers = {"Authorization": "Bearer demo-alice"}
    api.post("/predict", headers=headers, json={"request_id": "query-0001"})
    report = {"query_id": "query-0001", "actual_min": 60}
    assert api.post("/feedback", headers={"Authorization": "Bearer demo-bob"}, json=report).status_code == 404
    assert api.post("/feedback", headers=headers, json=report).json()["outside_domain"]
    api.post("/feedback", headers=headers, json=report)
    assert len(api.app.state.store.reports) == 1
    saved = next(iter(api.app.state.store.reports.values()))
    assert saved["context"]["temp_c"] == 19
    assert saved["simulation"]
    assert api.post("/feedback", headers=headers, json={**report, "actual_min": 10}).status_code == 409


def test_invalid_feedback_and_station():
    api = client()
    headers = {"Authorization": "Bearer demo-alice"}
    assert api.post("/feedback", headers=headers, json={"query_id": "query-0001", "actual_min": -1}).status_code == 422
    assert api.post("/predict", headers=headers, json={"request_id": "query-0001", "station_id": "other"}).status_code == 422


def test_production_refuses_memory_store():
    import pytest
    with pytest.raises(ValueError, match="Firestore"):
        create_app("production", MemoryStore())
