from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

import dev_fixtures as fixtures
from app.contracts import PredictResponse


class FakeDB:
    def __init__(self):
        self.records = {}
        self.commits = 0

    def document(self, path):
        def get():
            data = deepcopy(self.records.get(path))
            return SimpleNamespace(exists=data is not None, id=path.split("/")[-1], to_dict=lambda: data)
        return SimpleNamespace(path=path, get=get)

    def batch(self):
        operations = []

        def commit():
            # Emulate the create-only precondition and atomicity of the test double.
            for action, reference, data in operations:
                if action == "create" and reference.path in self.records:
                    raise ValueError("already exists")
            for action, reference, data in operations:
                if action == "create":
                    self.records[reference.path] = deepcopy(data)
                else:
                    self.records[reference.path].update(data)
            self.commits += 1
        return SimpleNamespace(create=lambda ref, data: operations.append(("create", ref, data)),
                               update=lambda ref, data: operations.append(("update", ref, data)), commit=commit)


def bundle():
    return fixtures.fixture_documents(fixtures.DEFAULT_UID, "unit-01",
                                      datetime(2026, 10, 17, 12, tzinfo=timezone.utc))


def test_fixtures_match_existing_contract_and_are_always_simulated():
    documents = bundle()
    assert len(documents) == 10
    for path, data in documents.items():
        assert data["simulation"] is True
        assert data["fixture_uid"] == fixtures.DEFAULT_UID
        if path.startswith("query_history/"):
            parsed = PredictResponse.model_validate(data)
            assert parsed.source == "mock" and parsed.model_version == "test-fixture-c345"
            assert data["query_id"] == path.split("/")[-1]
        else:
            assert data["message"].startswith("PRUEBA")
    assert len(fixtures.fixture_documents(fixtures.DEFAULT_UID, "unit-01", extra_queries=20)) == 30


@pytest.mark.parametrize("run_id", ["", "../other", "has spaces", "UPPER", "x" * 25])
def test_invalid_run_ids_are_rejected(run_id):
    with pytest.raises(ValueError):
        fixtures.fixture_documents(fixtures.DEFAULT_UID, run_id)


def test_seed_is_idempotent_and_preserves_existing_feedback_on_history():
    db = FakeDB()
    documents = bundle()
    assert len(fixtures.seed_documents(db, documents)["created"]) == 10
    path = next(path for path in documents if path.startswith("query_history/"))
    db.records[path]["actual_min"] = 15
    before = deepcopy(db.records)
    result = fixtures.seed_documents(db, documents)
    assert result["created"] == [] and len(result["preserved_without_changes"]) == 10
    assert db.records == before and db.commits == 1


def test_collision_prevents_the_entire_seed_batch():
    db = FakeDB()
    documents = bundle()
    db.records[list(documents)[-1]] = {"message": "No pertenece a la prueba"}
    before = deepcopy(db.records)
    with pytest.raises(ValueError, match="Colisión"):
        fixtures.seed_documents(db, documents)
    assert db.records == before and db.commits == 0


def test_seed_without_apply_is_offline_preview(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["dev_fixtures.py", "seed", "--run-id", "unit-01"])
    monkeypatch.setattr(fixtures, "connect_development", lambda *a, **k: pytest.fail("No debe conectar"))
    assert fixtures.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True and result["uid"] == fixtures.DEFAULT_UID


def test_remote_writes_require_matching_project_confirmation(monkeypatch):
    monkeypatch.delenv("FIRESTORE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    with pytest.raises(ValueError, match="confirm-dev-project"):
        fixtures.connect_development("arribo-development", fixtures.DEFAULT_UID, "other-project", writing=True)


def test_real_cli_rejects_emulator_configuration(monkeypatch):
    monkeypatch.setenv("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8082")
    with pytest.raises(ValueError, match="No mezcles"):
        fixtures.connect_development("arribo-development", fixtures.DEFAULT_UID)


def test_retire_only_changes_this_runs_alerts_and_does_not_delete():
    db = FakeDB()
    documents = bundle()
    fixtures.seed_documents(db, documents)
    db.records["alerts/other-alert"] = {"active": True}
    histories = {path: deepcopy(data) for path, data in db.records.items() if path.startswith("query_history/")}
    result = fixtures.retire_alerts(db, documents)
    assert len(result["retired"]) == 5 and result["deleted"] == 0
    assert db.records["alerts/other-alert"]["active"] is True
    assert all(db.records[path] == data for path, data in histories.items())


def test_verify_reports_missing_fixtures_and_missing_expected_feedback():
    db = FakeDB()
    documents = bundle()
    assert len(fixtures.inspect_documents(db, documents)["failures"]) == 10
    fixtures.seed_documents(db, documents)
    assert fixtures.inspect_documents(db, documents)["failures"] == []
    assert len(fixtures.inspect_documents(db, documents, ["online"])["failures"]) == 1


def test_verify_returns_failures_for_malformed_feedback_without_crashing():
    db = FakeDB()
    documents = bundle()
    fixtures.seed_documents(db, documents)
    path = next(path for path in documents if path.startswith("query_history/"))
    query = db.records[path]
    query["actual_min"] = 15
    db.records[f"feedback/{fixtures.DEFAULT_UID}/entries/{query['query_id']}"] = {
        "uid": fixtures.DEFAULT_UID, "query_id": query["query_id"],
        "station_id": query["station_id"], "simulation": True,
        "predicted_min": query["waiting_time_min"], "actual_min": 15,
        "context": query["context"], "query_at": query["requested_at"],
        "model_version": query["model_version"], "error_min": None,
    }
    failures = fixtures.inspect_documents(db, documents)["failures"]
    assert len(failures) == 3
