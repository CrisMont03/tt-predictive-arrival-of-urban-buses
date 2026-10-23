"""Section E: no credentials, remote writes or trained model are required."""
from copy import deepcopy
from datetime import timedelta
import csv
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.artifacts import digest, download_package, json_bytes, validate_manifest
from app.integration_predictor import IntegrationPredictor
from app.main import create_app
from app.model_inputs import timestamp, transform, fit_scaler
from app.prediction_cache import cache_key
from Dataset.preparar_modelo import read_rows
from Infra.prepare import plan
from Modelo.operacion.data import feedback_rows, prepare_mixed
from Modelo.operacion.evaluation import compare
from Modelo.operacion.fixtures import run_fixture
from Modelo.operacion.promotion import CloudRunDriver, promote, rollback
from Modelo.operacion.registry import ConcurrentRun, Lease, LocalRegistry
from Modelo.retrain_job import monthly_benchmark


def report(day=12, hour=12, minutes=10, **changes):
    return dict(uid="isolated-user", query_id=f"isolated-{day}-{hour}", station_id="rio_consulado",
                query_at=f"2026-10-{day:02}T{hour:02}:00:00Z", received_at=f"2026-10-{day:02}T{hour:02}:15:00Z",
                actual_min=minutes, simulation=False,
                context=dict(hour=hour-6, day_of_week=0, is_weekend=0, is_holiday=0), **changes)


def camera_file(tmp_path):
    records = []
    for day in range(1, 11):
        for track in (-1, 1, 2, 3):
            minute = max(0, track) * 15
            end = minute + (5 if track != -1 else 0)
            records.append(dict(track_id_persona=str(track), video_source=f"video-{day}",
                                record_type="bus_only" if track == -1 else "user_wait", wait_observed=int(track != -1),
                                station_id="rio_consulado", camera_id="camera", arrival_user=f"2026-10-{day:02}T12:{minute:02}:00Z",
                                bus_arrival=f"2026-10-{day:02}T12:{end:02}:00Z", waiting_time_min=5 if track != -1 else 0,
                                is_holiday=0, precipitation_mm=0, temp_c=20, humidity=65))
    path = tmp_path / "camera.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    return read_rows(path)


def test_readiness_never_claims_demo_or_missing_model_is_ready():
    with TestClient(create_app("demo")) as api:
        assert api.get("/health").status_code == 200
        assert api.get("/ready").status_code == 503
    from test_query_flow import Adapter
    with TestClient(create_app("production", Adapter())) as api:
        assert api.get("/ready").status_code == 503
    predictor = SimpleNamespace(metadata={"model_version": "approved-unit-test", "release_id": "r1"})
    with TestClient(create_app("production", Adapter(), predictor)) as api:
        assert api.get("/ready").json()["release_id"] == "r1"


class FakeStorage:
    def __init__(self, values):
        self.values, self.calls = values, []

    def bucket(self, name):
        self.calls.append(name)
        return self

    def blob(self, name, generation=None):
        data = self.values[(name, generation)]
        def download(**kwargs):
            assert kwargs["if_generation_match"] == generation
            return data
        return SimpleNamespace(size=len(data), reload=lambda **kw: None, download_as_bytes=download)


def package():
    files = {"model.keras": b"unit-only", "metadata.json": b"{}"}
    manifest = dict(format="arribo-package-v1", schema_version="arribo-sequences-v2", simulation=False,
                    release_id="release-1", files={key: dict(generation=i+1, size=len(value), sha256=digest(value))
                    for i, (key, value) in enumerate(files.items())})
    data = json_bytes(manifest)
    values = {("releases/r/manifest.json", 9): data}
    values.update({("releases/r/" + key, manifest["files"][key]["generation"]): value for key, value in files.items()})
    return data, values


def test_pinned_download_checks_generation_hash_and_never_uses_latest(tmp_path):
    data, values = package()
    path, manifest = download_package("gs://models/releases/r/manifest.json", 9, digest(data),
                                      client=FakeStorage(values), directory=tmp_path)
    assert (path / "model.keras").read_bytes() == b"unit-only"
    assert manifest["release_id"] == "release-1"


@pytest.mark.parametrize("failure", ["manifest_hash", "model_hash", "simulation", "traversal", "generation"])
def test_pinned_download_rejects_tampering(tmp_path, failure):
    data, values = package()
    manifest = json.loads(data)
    if failure == "model_hash":
        values[("releases/r/model.keras", 1)] = b"tampered!"
    if failure == "simulation":
        manifest["simulation"] = True
    if failure == "traversal":
        manifest["files"]["../escape"] = manifest["files"]["model.keras"]
    if failure == "generation":
        manifest["files"]["model.keras"]["generation"] = 0
    data = json_bytes(manifest)
    values[("releases/r/manifest.json", 9)] = data
    with pytest.raises(ValueError):
        download_package("gs://models/releases/r/manifest.json", 9, "0" * 64 if failure == "manifest_hash" else digest(data),
                         client=FakeStorage(values), directory=tmp_path)


def test_feedback_filters_cutoff_duplicates_simulations_and_preserves_receipt():
    original = report()
    fake = {**original, "query_id": "simulation", "simulation": True}
    future = {**original, "query_id": "future", "received_at": "2026-11-01T00:00:00Z"}
    rows, reasons = feedback_rows([original, original, fake, future], "2026-10-20T00:00:00Z")
    assert len(rows) == 1 and rows[0]["loss_weight"] == .5
    assert "bus_arrival" not in rows[0]
    assert reasons["duplicate"] == reasons["simulation_or_unknown_origin"] == reasons["domain_or_cutoff"] == 1


def test_mixed_preparation_preserves_sentinels_weights_and_causality(tmp_path):
    camera = camera_file(tmp_path)
    feedback = [report(day, hour) for day in range(11, 16) for hour in (12, 13)]
    rows, quality = prepare_mixed(camera, feedback, "2026-10-20T00:00:00Z")
    assert quality["can_train"]
    assert len([row for row in rows if row["record_type"] == "bus_only"]) == 10
    for row in rows:
        assert row["operational_inputs"]["bus_mask"] == [[0] * 4] * 10
        assert row["operational_inputs"]["past_wait_mask"][-1] == [0]
        if row["record_type"] == "bus_only":
            assert row["loss_weight"] == 0
        if row["source"] == "feedback":
            assert "bus_arrival" not in row
            assert row["loss_weight"] in (0, .5)
        for step in row["operational_inputs"]["provenance"]:
            if step["kind"] == "observed":
                assert timestamp(step["received_at"]) < timestamp(row["event_at"])
    training = [row for row in rows if row["split"] == "train"]
    scaler = fit_scaler(training)
    assert len(transform(rows[-1]["operational_inputs"], scaler)[0]) == 27


def test_future_feedback_does_not_change_existing_examples_or_scaler(tmp_path):
    camera = camera_file(tmp_path)
    first, quality = prepare_mixed(camera, [], "2026-10-12T00:00:00Z")
    second, _ = prepare_mixed(camera, [report(13)], "2026-10-12T00:00:00Z")
    assert first == second


def test_monthly_benchmark_is_independent_and_does_not_use_future_reports():
    reports = [report(day, hour) for day in range(1, 11) for hour in (12, 13)]
    benchmark = monthly_benchmark(reports, "2026-10-20T00:00:00Z", minimum=2)
    assert benchmark["simulation"] is False
    for case in benchmark["cases"]:
        assert all(timestamp(item["received_at"]) < timestamp(case["query_at"]) for item in case["observations"])
    with pytest.raises(ValueError, match="insuficiente"):
        monthly_benchmark([], "2026-10-20T00:00:00Z")


@pytest.mark.parametrize("scenario,decision", [("better", "fixture_improves"), ("equal", "fixture_keeps"), ("worse", "fixture_keeps")])
def test_fixture_cycle_is_repeatable_and_never_publishable(tmp_path, scenario, decision):
    first = run_fixture(tmp_path, scenario)
    second = run_fixture(tmp_path, scenario)
    assert first == second and first["attempts"] == 1
    assert first["status"] == "evaluated"
    assert first["evaluation"]["decision"] == decision
    assert not first["evaluation"]["publishable"]


def test_invalid_fixture_records_failure_and_insufficient_data_skips(tmp_path):
    with pytest.raises(ValueError, match="Predicción"):
        run_fixture(tmp_path, "invalid")
    value, _ = LocalRegistry(tmp_path / "registry").read("runs/fixture-invalid")
    assert value["status"] == "failed"
    assert run_fixture(tmp_path, "insufficient")["status"] == "skipped"


def test_cas_lease_rejects_concurrency_and_lost_owner(tmp_path):
    registry = LocalRegistry(tmp_path)
    now = [100.0]
    first = Lease(registry, "one", clock=lambda: now[0]).acquire()
    with pytest.raises(ConcurrentRun):
        Lease(registry, "two", clock=lambda: now[0]).acquire()
    first.renew()
    now[0] = 300
    second = Lease(registry, "two", clock=lambda: now[0]).acquire()
    with pytest.raises(ConcurrentRun):
        first.check()
    second.check()


class FakeDriver:
    def __init__(self, fail_at=None):
        self.current = {"old": 100}
        self.calls, self.checks, self.fail_at = [], 0, fail_at

    def traffic(self):
        return self.current.copy()

    def deploy(self, *args):
        self.calls.append("deploy")
        return dict(revision="new", url="https://isolated.invalid")

    def ready(self, *args):
        self.checks += 1
        return self.checks != self.fail_at

    def switch(self, value):
        self.calls.append(value.copy())
        self.current = value.copy()


def promotion_args():
    return dict(package=dict(release_id="r2", uri="gs://models/r2/manifest.json", generation=1, sha256="a" * 64),
                evaluation=dict(simulation=False, publishable=True, candidate_version="v2", incumbent_version="v1"),
                expected_incumbent="v1", model_version="v2", enabled=True)


def seed(registry):
    registry.cas("deployment/current", dict(model_version="v1", package={"release_id": "r1"},
                 deployment={"revision": "old", "url": "https://old.invalid"}), 0)


def test_promotion_and_manual_rollback_preserve_prior_release(tmp_path):
    registry, driver = LocalRegistry(tmp_path), FakeDriver()
    seed(registry)
    assert promote(registry, driver, **promotion_args())["status"] == "active"
    assert driver.current == {"new": 100}
    assert rollback(registry, driver, enabled=True)["model_version"] == "v1"
    assert driver.current == {"old": 100}
    assert registry.read("promotion/suspended")[0]["suspended"]


@pytest.mark.parametrize("failure", [1, 2])
def test_unready_revision_never_remains_active(tmp_path, failure):
    registry, driver = LocalRegistry(tmp_path), FakeDriver(failure)
    seed(registry)
    with pytest.raises(ValueError):
        promote(registry, driver, **promotion_args())
    assert driver.current == {"old": 100}
    assert registry.read("deployment/current")[0]["model_version"] == "v1"


def test_simulation_cannot_promote_and_disabled_mode_has_no_side_effects(tmp_path):
    registry, driver = LocalRegistry(tmp_path), FakeDriver()
    args = promotion_args()
    args["enabled"] = False
    assert promote(registry, driver, **args)["status"] == "disabled"
    args["enabled"] = True
    args["evaluation"]["simulation"] = True
    with pytest.raises(ValueError, match="simulación"):
        promote(registry, driver, **args)
    assert driver.calls == []


def test_infrastructure_is_separated_and_schedule_is_opt_in():
    config = json.loads((ROOT / "Infra" / "config.example.json").read_text())
    bootstrap = plan(config, "bootstrap")
    assert not any("scheduler jobs create" in " ".join(step["command"]) for step in bootstrap)
    assert not any("roles/owner" in step["command"] or "roles/editor" in step["command"] for step in bootstrap)
    jobs = " ".join(" ".join(step["command"]) for step in plan(config, "jobs"))
    assert "ARRIBO_AUTO_PROMOTION=false" in jobs
    scheduler = " ".join(plan(config, "schedule")[0]["command"])
    assert "0 3 1 * *" in scheduler and "--oauth-service-account-email" in scheduler


def test_release_identity_changes_cache_key():
    model = IntegrationPredictor()
    base = dict(station_id="rio_consulado", context={}, prepared={}, observations=[], simulation=False)
    assert cache_key(metadata={**model.metadata, "release_id": "one"}, **base) != cache_key(metadata={**model.metadata, "release_id": "two"}, **base)


@pytest.mark.parametrize("value,decision,publishable", [(12, "candidate_improves", True), (16, "keep_incumbent", False), (20, "keep_incumbent", False)])
def test_real_comparison_requires_strict_improvement_on_same_raw_cases(value, decision, publishable):
    metadata = dict(schema_version="arribo-sequences-v2", simulation=False, eligible=True,
                    training_sample_ids=["train"], calibration_sample_ids=["validation"],
                    training_cutoff="2026-10-01T00:00:00Z")
    calls = []
    def prediction(name, result):
        def predict(context, observations, **kwargs):
            calls.append((name, deepcopy(context), deepcopy(observations), kwargs))
            return result, (0, 45)
        return predict
    candidate = SimpleNamespace(metadata={**metadata, "model_version": "new"}, predict=prediction("new", value))
    incumbent = SimpleNamespace(metadata={**metadata, "model_version": "old"}, predict=prediction("old", 16))
    benchmark = dict(format="arribo-benchmark-v1", purpose="monthly", simulation=False, cases=[
        dict(sample_id="heldout", simulation=False, station_id="rio_consulado", query_at="2026-10-20T00:00:00Z",
             target=12, context=report()["context"], observations=[report()])])
    result = compare(benchmark, candidate, incumbent)
    assert result["decision"] == decision and result["publishable"] == publishable
    assert calls[0][1:] == calls[2][1:] and calls[1][1:] == calls[3][1:]
    candidate.metadata["training_sample_ids"] = ["heldout"]
    with pytest.raises(ValueError, match="utilizado"):
        compare(benchmark, candidate, incumbent)


def test_manual_initial_approval_is_not_automatic():
    args = promotion_args()
    args["evaluation"] = dict(simulation=False, publishable=False, decision="manual_initial_approval", candidate_version="v2", incumbent_version=None)
    args["expected_incumbent"] = None
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as path:
        with pytest.raises(ValueError, match="mejora"):
            promote(LocalRegistry(path), FakeDriver(), **args)


def test_benchmark_holdout_excludes_bus_mates_from_training(tmp_path):
    camera = camera_file(tmp_path)
    held = camera[2]
    rows, _ = prepare_mixed(camera, [], "2026-10-20T00:00:00Z", benchmark_ids=[held["sample_id"]])
    assert held["sample_id"] not in {row["sample_id"] for row in rows}


def test_crashed_traffic_transfer_recovers_and_suspends_promotion(tmp_path):
    registry, driver = LocalRegistry(tmp_path), FakeDriver()
    seed(registry)
    previous = registry.read("deployment/current")[0]
    registry.cas("deployments/r2", dict(status="switching", previous_traffic={"old": 100}, previous=previous), 0)
    driver.current = {"new": 100}
    assert promote(registry, driver, **promotion_args())["status"] == "recovered_after_interruption"
    assert driver.current == {"old": 100}
    assert registry.read("promotion/suspended")[0]["suspended"]


def test_real_synthetic_training_and_loading_if_ml_is_installed(tmp_path):
    pytest.importorskip("tensorflow", reason="Entorno ML pendiente; no se instala ni entrena el modelo final")
    pytest.importorskip("xgboost")
    from app.artifacts import immutable_json
    from app.predictor import Predictor
    from Modelo.retrain_job import train_real
    rows, quality = prepare_mixed(camera_file(tmp_path), [], "2026-10-20T00:00:00Z")
    preparation = tmp_path / "prepared"
    preparation.mkdir()
    content = b"".join(json_bytes(row) + b"\n" for row in rows)
    (preparation / "sequences.jsonl").write_bytes(content)
    immutable_json(preparation / "quality.json", {**quality, "simulation": True, "sequences_sha256": digest(content)})
    candidate = train_real(preparation, tmp_path / "candidate", "synthetic-only", synthetic=True, epochs=1)
    assert candidate.metadata["simulation"] and not candidate.metadata["eligible"]
    with pytest.raises(ValueError, match="aprobado"):
        Predictor(tmp_path / "candidate")


def test_tagged_revision_urls_survive_traffic_switch_and_rollback():
    statuses = [dict(revision="old", percent=100, tag="old-tag"), dict(revision="new", percent=0, tag="new-tag")]
    values = CloudRunDriver.targets({"new": 100}, statuses)
    assert next(row for row in values if row["tag"] == "new-tag")["percent"] == 100
    assert next(row for row in values if row["tag"] == "old-tag")["percent"] == 0
