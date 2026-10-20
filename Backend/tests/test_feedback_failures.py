from fastapi.testclient import TestClient
from google.api_core.exceptions import Aborted
import pytest

from app.main import create_app
from app.store import FeedbackTemporarilyUnavailable, FirestoreStore


def test_temporary_failure_is_not_reported_as_duplicate_conflict(monkeypatch):
    api = TestClient(create_app("demo"))
    headers = {"Authorization": "Bearer demo-alice"}
    api.post("/predict", headers=headers, json={"request_id": "query-retry-01"})
    store = api.app.state.store
    original = store.save_feedback

    def unavailable(*args):
        raise FeedbackTemporarilyUnavailable()

    monkeypatch.setattr(store, "save_feedback", unavailable)
    body = {"query_id": "query-retry-01", "actual_min": 15}
    result = api.post("/feedback", headers=headers, json=body)
    assert result.status_code == 503 and result.json()["code"] == "SERVICE_ERROR"
    assert not store.reports
    monkeypatch.setattr(store, "save_feedback", original)
    assert api.post("/feedback", headers=headers, json=body).status_code == 200
    assert len(store.reports) == 1


def test_sdk_exhausted_retry_value_error_remains_temporary(monkeypatch):
    from firebase_admin import firestore

    class Database:
        def collection(self, *args):
            return self

        def document(self, *args):
            return self

        def transaction(self):
            return object()

    def exhausted(function):
        def run(transaction):
            raise ValueError("transaction exhausted") from Aborted("lock contention")
        return run

    monkeypatch.setattr(firestore, "transactional", exhausted)
    with pytest.raises(FeedbackTemporarilyUnavailable):
        FirestoreStore(Database()).save_feedback("uid", "qid", {})
