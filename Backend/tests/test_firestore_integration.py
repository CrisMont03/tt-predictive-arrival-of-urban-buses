"""Real Firestore adapter on the local emulator. Only token verification is mocked.

Run from Codigo/Firebase: npm run test:integration. No real Firebase writes.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import FirestoreStore
from dev_fixtures import (CASES, DEFAULT_UID, fixture_documents, inspect_documents,
                          retire_alerts, seed_documents)


@pytest.fixture
def environment(monkeypatch):
    host = os.getenv("FIRESTORE_EMULATOR_HOST")
    if not host:
        pytest.skip("Ejecuta npm run test:integration en Codigo/Firebase")
    if host not in {"127.0.0.1:8082", "localhost:8082"}:
        pytest.fail("Solo se permiten pruebas contra Firestore local en 8082")
    from firebase_admin import auth
    from google.auth.credentials import AnonymousCredentials
    from google.cloud.firestore import Client
    db = Client(project="demo-arribo", credentials=AnonymousCredentials())
    run_id = f"auto-{uuid4().hex[:10]}"
    documents = fixture_documents(DEFAULT_UID, run_id)
    seed_documents(db, documents)
    monkeypatch.delenv("ARRIBO_MODEL_DIR", raising=False)

    def verify(token, **kwargs):
        assert kwargs["check_revoked"] is True
        if token == "test-owner":
            return {"uid": DEFAULT_UID}
        if token == "test-other":
            return {"uid": "other-emulator-user"}
        raise auth.InvalidIdTokenError("invalid test token")
    monkeypatch.setattr(auth, "verify_id_token", verify)
    store = FirestoreStore(db)
    with TestClient(create_app("production", store=store)) as api:
        yield db, documents, store, api, run_id
    # Remove only this test's known emulator documents, never clear the database.
    for path in documents:
        db.document(path).delete()
    for case in CASES:
        db.document(f"feedback/{DEFAULT_UID}/entries/arribo-c345-{run_id}-{case}").delete()
    db.close()


def owner_headers():
    return {"Authorization": "Bearer test-owner"}


def query_id(run_id, case="online"):
    return f"arribo-c345-{run_id}-{case}"


def test_section_d_cache_history_feedback_on_real_firestore_adapter(environment, monkeypatch):
    from app.integration_predictor import IntegrationPredictor, integration_context
    db, documents, store, original_api, run_id = environment
    clock = [datetime.now(timezone.utc)]
    predictor = IntegrationPredictor()
    ids = [f"arribo-d-{run_id}-{i}" for i in range(3)]
    cache_keys = []
    set_cache = store.set_cache
    def tracked_cache(key, value):
        cache_keys.append(key)
        set_cache(key, value)
    monkeypatch.setattr(store, "set_cache", tracked_cache)
    try:
        with TestClient(create_app("production", store, predictor, allow_integration=True,
                                   clock=lambda: clock[0], context_provider=integration_context)) as api:
            def request(qid):
                response = api.post("/predict", headers=owner_headers(), json={"request_id": qid})
                assert response.status_code == 200
                return response.json()
            first = request(ids[0])
            clock[0] += timedelta(seconds=1)
            second = request(ids[1])
            assert first["source"] == "mock" and second["source"] == "cache"
            assert first["simulation"] and second["simulation"]
            assert predictor.calls == 1
            assert request(ids[1]) == second
            snapshot = store.get_query(DEFAULT_UID, ids[1])
            assert snapshot["requested_at"] > snapshot["timestamp"]
            assert snapshot["context"] == first["context"]
            body = {"query_id": ids[1], "actual_min": 15}
            for _ in range(2):
                assert api.post("/feedback", headers=owner_headers(), json=body).status_code == 200
            report = db.document(f"feedback/{DEFAULT_UID}/entries/{ids[1]}").get().to_dict()
            assert report["simulation"] and report["query_at"] == snapshot["requested_at"]
            assert store.get_query(DEFAULT_UID, ids[1])["actual_min"] == 15
            assert api.post("/feedback", headers={"Authorization": "Bearer test-other"}, json=body).status_code == 404
            assert api.post("/feedback", headers=owner_headers(), json={**body, "actual_min": 16}).status_code == 409
            assert all(r["query_id"] != ids[1] for r in store.recent_observations("rio_consulado", clock[0] - timedelta(hours=2)))
            clock[0] += timedelta(minutes=6)
            assert request(ids[2])["source"] == "mock"
            assert predictor.calls == 2
    finally:
        for qid in ids:
            store.query_ref(DEFAULT_UID, qid).delete()
            db.document(f"feedback/{DEFAULT_UID}/entries/{qid}").delete()
        for key in set(cache_keys):
            db.document(f"predictions/{key}").delete()


def test_adapter_history_order_and_repeat_seed_preserve_data(environment):
    db, documents, store, api, run_id = environment
    rows = list(db.collection("query_history").document(DEFAULT_UID).collection("queries")
                .order_by("requested_at", direction="DESCENDING").stream())
    own = [row for row in rows if row.to_dict().get("fixture_run") == run_id]
    assert len(own) == 5
    assert own[0].id == query_id(run_id)
    before = store.get_query(DEFAULT_UID, query_id(run_id))
    result = seed_documents(db, documents)
    assert result["created"] == []
    assert store.get_query(DEFAULT_UID, query_id(run_id)) == before


def test_feedback_snapshot_history_and_idempotent_retry(environment):
    db, documents, store, api, run_id = environment
    body = {"query_id": query_id(run_id), "actual_min": 15}
    first = api.post("/feedback", headers=owner_headers(), json=body)
    assert first.status_code == 200 and first.json()["simulation"] is True
    report_ref = db.document(f"feedback/{DEFAULT_UID}/entries/{body['query_id']}")
    original_report = report_ref.get().to_dict()
    assert api.post("/feedback", headers=owner_headers(), json=body).status_code == 200
    assert report_ref.get().to_dict() == original_report
    assert inspect_documents(db, documents, ["online"])["failures"] == []
    assert store.get_query(DEFAULT_UID, body["query_id"])["actual_min"] == 15
    conflict = api.post("/feedback", headers=owner_headers(), json={**body, "actual_min": 16})
    assert conflict.status_code == 409 and conflict.json()["code"] == "FEEDBACK_EXISTS"
    assert report_ref.get().to_dict() == original_report


def test_provider_provenance_is_persisted_with_feedback_without_changing_original_query(environment):
    db, documents, store, api, run_id = environment
    identifier = query_id(run_id)
    sources = {"traffic": {"status": "unavailable", "reason": "test-fixture", "checked_at": "2026-10-17T19:00:00Z"}}
    original_ref = db.document(f"query_history/{DEFAULT_UID}/queries/{identifier}")
    original_ref.update({"context_sources": sources})
    for _ in range(2):
        assert api.post("/feedback", headers=owner_headers(), json={"query_id": identifier, "actual_min": 15}).status_code == 200
    saved = db.document(f"feedback/{DEFAULT_UID}/entries/{identifier}").get().to_dict()
    assert saved["context_sources"] == sources
    assert original_ref.get().to_dict()["context_sources"] == sources
    assert inspect_documents(db, documents, ["online"])["failures"] == []


@pytest.mark.parametrize("declaration,expected", [("SE SUSPENDE LA", 0), ("SE ACTIVA LA", 1)])
def test_old_environmental_state_flows_to_feedback_and_survives_identical_retry(environment, declaration, expected):
    from app.environmental import environmental_state
    from app.context import traffic_value

    db, documents, store, api, run_id = environment
    title = f"{declaration} CONTINGENCIA AMBIENTAL POR OZONO EN LA ZONA METROPOLITANA DEL VALLE DE MÉXICO"
    payload = (f'<article><time datetime="2020-09-13 15:45:00"></time><h2>{title}</h2>'
               '<a href="/comisionambiental/prensa/unit-test">Leer</a></article>')
    state, evidence = environmental_state(payload, datetime.now(timezone.utc))
    assert state == expected and evidence["notice_is_old"]
    ratio, traffic_evidence = traffic_value({"flowSegmentData": {
        "currentSpeed": 20, "freeFlowSpeed": 40, "confidence": 1, "roadClosure": False,
        "coordinates": {"coordinate": [{"latitude": 19.4624, "longitude": -99.1297}]},
    }})
    identifier = query_id(run_id)
    history_ref = db.document(f"query_history/{DEFAULT_UID}/queries/{identifier}")
    sources = {"environmental": evidence, "traffic": traffic_evidence}
    # Emulator-only simulated query; never enrich existing remote fixtures retroactively.
    history_ref.update({"context.env_alert_active": state, "context.traffic_density": ratio,
                        "context_sources": sources})
    body = {"query_id": identifier, "actual_min": 15}
    assert api.post("/feedback", headers=owner_headers(), json=body).status_code == 200
    report_ref = db.document(f"feedback/{DEFAULT_UID}/entries/{identifier}")
    first = report_ref.get().to_dict()
    assert first["context"]["env_alert_active"] == expected
    assert first["context"]["traffic_density"] == 0.5
    assert first["context_sources"] == sources
    assert first["context_sources"]["traffic"]["geometry"]["travel_direction_verified"] is False
    assert api.post("/feedback", headers=owner_headers(), json=body).status_code == 200
    assert report_ref.get().to_dict() == first
    assert history_ref.get().to_dict()["actual_min"] == 15


def test_feedback_requires_authentication_and_original_owner(environment):
    db, documents, store, api, run_id = environment
    body = {"query_id": query_id(run_id), "actual_min": 15}
    assert api.post("/feedback", json=body).status_code == 401
    assert api.post("/feedback", headers={"Authorization": "Bearer demo-alice"}, json=body).status_code == 401
    assert api.post("/feedback", headers={"Authorization": "Bearer test-other"}, json=body).status_code == 404
    assert api.post("/feedback", headers=owner_headers(), json={**body, "station_id": "other"}).status_code == 422
    assert not db.document(f"feedback/{DEFAULT_UID}/entries/{body['query_id']}").get().exists


def test_no_model_returns_model_not_ready_for_a_new_request(environment):
    db, documents, store, api, run_id = environment
    health = api.get("/health").json()
    assert health["mode"] == "production" and health["model_ready"] is False
    new_id = f"arribo-c345-{run_id}-new-prediction"
    response = api.post("/predict", headers=owner_headers(), json={"request_id": new_id})
    assert response.status_code == 503 and response.json()["code"] == "MODEL_NOT_READY"
    assert store.get_query(DEFAULT_UID, new_id) is None


def test_zero_long_wait_and_simulated_reports_are_not_recent_training_context(environment):
    db, documents, store, api, run_id = environment
    for case, value in [("online", 0), ("long", 60)]:
        response = api.post("/feedback", headers=owner_headers(), json={"query_id": query_id(run_id, case), "actual_min": value})
        assert response.status_code == 200
        assert response.json()["outside_domain"] is (value > 45)
    for value in [-1, 181]:
        assert api.post("/feedback", headers=owner_headers(), json={"query_id": query_id(run_id, "offline"), "actual_min": value}).status_code == 422
    reports = store.recent_observations("rio_consulado", datetime.now(timezone.utc) - timedelta(hours=2))
    assert all(report["simulation"] is False for report in reports)
    assert inspect_documents(db, documents, ["online", "long"])["failures"] == []


def test_concurrent_same_feedback_creates_one_report(environment):
    db, documents, store, api, run_id = environment
    body = {"query_id": query_id(run_id, "retry"), "actual_min": 14.5}
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: api.post("/feedback", headers=owner_headers(), json=body), range(2)))
    # The emulator may exhaust transaction retries under lock contention. A transient
    # failure is retriable, never a different-value conflict or a successful receipt.
    assert all(response.status_code in {200, 503} for response in results), [
        (response.status_code, response.json()) for response in results
    ]
    for response in results:
        if response.status_code == 503:
            assert response.json()["code"] == "SERVICE_ERROR"
        assert api.post("/feedback", headers=owner_headers(), json=body).status_code == 200
    entries = list(db.collection("feedback").document(DEFAULT_UID).collection("entries").stream())
    assert len([entry for entry in entries if entry.id == body["query_id"]]) == 1
    assert inspect_documents(db, documents, ["retry"])["failures"] == []


def test_alerts_have_visible_and_hidden_cases_and_can_be_retired(environment):
    db, documents, store, api, run_id = environment
    from google.cloud.firestore_v1.base_query import FieldFilter
    now = datetime.now(timezone.utc)
    rows = db.collection("alerts").where(filter=FieldFilter("station_id", "==", "rio_consulado"))\
        .where(filter=FieldFilter("active", "==", True)).stream()
    visible = [row.to_dict() for row in rows if row.to_dict().get("fixture_run") == run_id
               and row.to_dict()["expires_at"] > now]
    assert {row["severity"] for row in visible} == {"info", "warning", "critical"}
    assert all(row["message"].startswith("PRUEBA") for row in visible)
    assert len(retire_alerts(db, documents)["retired"]) == 5
    assert all(not db.document(path).get().to_dict()["active"] for path in documents if path.startswith("alerts/"))
