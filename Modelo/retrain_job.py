"""Monthly maintenance entry point. Default: isolated fixture, no cloud writes or ML."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Backend")]
from app.artifacts import digest, download_package, gcs_parts, immutable_json, json_bytes, publish_package, read_json
from app.model_inputs import SCHEMA_VERSION, select_observations, timestamp
from app.predictor import Predictor
from Dataset.preparar_modelo import read_rows, read_manifest
from Modelo.operacion.data import feedback_rows, prepare_mixed
from Modelo.operacion.evaluation import compare
from Modelo.operacion.registry import ConcurrentRun, GCSRegistry, Lease, LocalRegistry


def pinned_object(reference, client, *, limit=100_000_000):
    bucket, name = gcs_parts(reference["uri"])
    generation = int(reference["generation"])
    if generation < 1:
        raise ValueError("La fuente debe fijar una generación")
    blob = client.bucket(bucket).blob(name, generation=generation)
    blob.reload(if_generation_match=generation)
    if blob.size is None or not 0 < blob.size <= limit:
        raise ValueError("Fuente demasiado grande o vacía")
    data = blob.download_as_bytes(if_generation_match=generation)
    if digest(data) != reference["sha256"]:
        raise ValueError("Hash de fuente incorrecto")
    return data


def monthly_benchmark(reports, cutoff, *, minimum=30):
    rows, _ = feedback_rows(reports, cutoff)
    days = sorted({timestamp(row["event_at"]).astimezone(ZoneInfo("America/Mexico_City")).date() for row in rows})
    if len(days) < 2:
        raise ValueError("Benchmark insuficiente: faltan jornadas independientes de feedback")
    tail = set(days[max(1, int(len(days) * .8)):])
    selected = [row for row in rows if timestamp(row["event_at"]).astimezone(ZoneInfo("America/Mexico_City")).date() in tail]
    if len(selected) < minimum:
        raise ValueError("Benchmark insuficiente: faltan observaciones recientes")
    cases = []
    for row in sorted(selected, key=lambda item: (timestamp(item["event_at"]), item["sample_id"])):
        observations = select_observations(reports, row["event_at"], row["station_id"], 30)
        cases.append(dict(sample_id=row["sample_id"], station_id=row["station_id"], query_at=row["event_at"],
                          context=row["context"], target=row["target"], simulation=False,
                          observations=observations))
    return dict(format="arribo-benchmark-v1", purpose="monthly", simulation=False, cases=cases)


def train_real(preparation, output, run_id, *, synthetic=False, epochs=50):
    command = [sys.executable, str(ROOT / "Modelo" / "entrenar.py"), "--data", str(preparation),
               "--output", str(output), "--version", run_id, "--epochs", str(epochs)]
    if synthetic:
        command.append("--synthetic")
    subprocess.run(command, check=True)
    return Predictor(output, require_approved=False)


def cycle(registry, work, *, run_id, inputs, prepare, train, evaluate, upload=None):
    """Dependencies injectable for deterministic tests; no function promotes a model."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    key = "runs/" + run_id
    with Lease(registry, run_id) as lease:
        previous, generation = registry.read(key)
        snapshot_hash = digest(json_bytes(inputs))
        if previous:
            if previous["snapshot_sha256"] != snapshot_hash:
                raise ValueError("Un periodo no puede cambiar su snapshot al reintentarse")
            if previous["status"] in {"evaluated", "skipped"}:
                return previous
        snapshot_key = "snapshots/" + run_id
        frozen, frozen_gen = registry.read(snapshot_key)
        if frozen is None:
            registry.cas(snapshot_key, inputs, 0)
        elif digest(json_bytes(frozen)) != snapshot_hash:
            raise ValueError("Snapshot inmutable incompatible")
        record = dict(run_id=run_id, snapshot_sha256=snapshot_hash, simulation=inputs["simulation"],
                      status="preparing", stages=[], attempts=(previous or {}).get("attempts", 0) + 1,
                      schema_version=SCHEMA_VERSION, started_at=datetime.now(timezone.utc).isoformat(),
                      code_revision=os.getenv("ARRIBO_CODE_REVISION", "local-unrecorded"),
                      dependencies={name: importlib.metadata.version(name) for name in ("firebase-admin", "httpx")})
        for name in ("tensorflow", "numpy", "xgboost"):
            try:
                record["dependencies"][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                record["dependencies"][name] = "not-installed"
        record["python"] = sys.version.split()[0]

        def save(status):
            nonlocal generation
            lease.check()
            record["status"] = status
            record["updated_at"] = datetime.now(timezone.utc).isoformat()
            record["stages"].append(status)
            generation = registry.cas(key, record, generation)

        save("snapshot")
        try:
            try:
                rows, quality = prepare(inputs)
            except ValueError as error:
                record["reason"] = str(error)
                save("skipped")
                return record
            record["quality"] = quality
            if not quality["can_train"]:
                record["reason"] = "Datos insuficientes en las particiones"
                save("skipped")
                return record
            prepared = work / "preparation"
            prepared.mkdir(exist_ok=True)
            content = b"".join(json_bytes(row) + b"\n" for row in rows)
            sequence = prepared / "sequences.jsonl"
            if sequence.exists() and sequence.read_bytes() != content:
                raise ValueError("Preparación no reproducible")
            if not sequence.exists():
                with sequence.open("xb") as stream:
                    stream.write(content)
            quality = {**quality, "sequences_sha256": digest(content)}
            immutable_json(prepared / "quality.json", quality)
            save("training")
            attempt = work / f"attempt-{record['attempts']}"
            output = attempt / "candidate"
            candidate_version = run_id + f"-a{record['attempts']}"
            candidate = train(prepared, output, candidate_version)
            save("evaluating")
            evaluation = evaluate(candidate)
            if evaluation["simulation"] != inputs["simulation"]:
                raise ValueError("Entornos de evaluación incompatibles")
            record["evaluation"] = evaluation
            record["dataset_sha256"] = digest(content)
            immutable_json(attempt / "evaluation.json", evaluation)
            save("candidate")
            if upload:
                lease.check()
                record["candidate_package"] = upload(output, candidate_version)
            # Approval and Cloud Run traffic belong to the separate promoter.
            save("evaluated")
            if not inputs["simulation"]:
                old, gen = registry.read("latest_evaluated")
                registry.cas("latest_evaluated", {"run_id": run_id}, gen)
            return record
        except Exception as error:
            record["error_type"] = type(error).__name__
            save("failed")
            raise


def cloud_run(args):
    from google.cloud import storage
    import firebase_admin
    from firebase_admin import firestore
    if args.project != args.confirm_dev_project or args.project != "arribo-68326":
        raise ValueError("Confirma explícitamente el proyecto de desarrollo permitido")
    client = storage.Client(project=args.project)
    spec_ref = dict(uri=os.environ["ARRIBO_TRAINING_SPEC_URI"],
                    generation=os.environ["ARRIBO_TRAINING_SPEC_GENERATION"],
                    sha256=os.environ["ARRIBO_TRAINING_SPEC_SHA256"])
    spec = json.loads(pinned_object(spec_ref, client, limit=1_000_000))
    if spec.get("simulation") is not False or spec.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Especificación real incompatible")
    registry = GCSRegistry(os.environ["ARRIBO_REGISTRY_URI"], client)
    now = datetime.now(ZoneInfo("America/Mexico_City"))
    period = args.period or now.strftime("%Y-%m")
    cutoff = timestamp(period + "-01T00:00:00-06:00")
    run_id = "monthly-" + period + "-" + digest(json_bytes(spec_ref))[:12]
    frozen, _ = registry.read("snapshots/" + run_id)
    root = Path(tempfile.mkdtemp(prefix="arribo-retrain-"))
    if frozen is None:
        if not firebase_admin._apps:
            firebase_admin.initialize_app(options={"projectId": args.project})
        from google.cloud.firestore_v1.base_query import FieldFilter
        reports = []
        for item in firestore.client().collection_group("entries").where(filter=FieldFilter("station_id", "==", "rio_consulado")).where(filter=FieldFilter("simulation", "==", False)).where(filter=FieldFilter("received_at", "<", cutoff)).stream():
            reports.append({**item.to_dict(), "uid": item.reference.parent.parent.id, "query_id": item.id})
        current, _ = registry.read("deployment/current")
        frozen = dict(simulation=False, cutoff=cutoff.isoformat(), spec=spec, spec_reference=spec_ref,
                      incumbent=current["package"] if current else spec.get("incumbent"),
                      feedback=sorted(reports, key=lambda item: (item["uid"], item["query_id"])))
    csv_path = root / "camera.csv"
    csv_path.write_bytes(pinned_object(frozen["spec"]["history"], client))
    manifest = None
    if frozen["spec"].get("coverage"):
        manifest_path = root / "coverage.csv"
        manifest_path.write_bytes(pinned_object(frozen["spec"]["coverage"], client))
        manifest = read_manifest(manifest_path)
    incumbent = None
    if frozen.get("incumbent"):
        ref = frozen["incumbent"]
        path, _ = download_package(ref["uri"], ref["generation"], ref["sha256"], client=client)
        incumbent = Predictor(path)
    benchmark_box = {}

    def prepare(inputs):
        if inputs["spec"].get("benchmark"):
            benchmark = json.loads(pinned_object(inputs["spec"]["benchmark"], client))
        else:
            benchmark = monthly_benchmark(inputs["feedback"], inputs["cutoff"], minimum=inputs["spec"].get("minimum_benchmark", 30))
        benchmark_box["value"] = benchmark
        # Strictly before benchmark queries, including calibration labels: no future fitting.
        fit_cutoff = min(timestamp(case["query_at"]) for case in benchmark["cases"])
        fit_cutoff = min(fit_cutoff, timestamp(inputs["cutoff"]))
        return prepare_mixed(read_rows(csv_path), inputs["feedback"], fit_cutoff,
                             window=inputs["spec"].get("window", 10), manifest=manifest,
                             benchmark_ids=[case["sample_id"] for case in benchmark["cases"]])

    result = cycle(registry, root, run_id=run_id, inputs=frozen, prepare=prepare,
                 train=lambda data, output, version: train_real(data, output, version, epochs=spec.get("epochs", 50)),
                 evaluate=lambda candidate: compare(benchmark_box["value"], candidate, incumbent),
                 upload=lambda output, version: publish_package(output, os.environ["ARRIBO_CANDIDATES_URI"], version, client=client))
    if result["status"] == "evaluated" and os.getenv("ARRIBO_PROMOTOR_JOB"):
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        job = os.environ["ARRIBO_PROMOTOR_JOB"]
        if not job.startswith(f"projects/{args.project}/locations/") or "/jobs/" not in job:
            raise ValueError("Job promotor de otro proyecto")
        response = AuthorizedSession(credentials).post("https://run.googleapis.com/v2/" + job + ":run", json={}, timeout=30)
        response.raise_for_status()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["fixture", "cloud"], default="fixture")
    parser.add_argument("--output", type=Path, default=ROOT / "Modelo" / "output" / "section-e")
    parser.add_argument("--project", default="arribo-68326")
    parser.add_argument("--confirm-dev-project")
    parser.add_argument("--period", help="YYYY-MM; reintentos conservan snapshot")
    parser.add_argument("--scenario", choices=["better", "equal", "worse", "invalid", "insufficient"], default="better")
    args = parser.parse_args()
    if args.mode == "fixture":
        from Modelo.operacion.fixtures import run_fixture
        result = run_fixture(args.output, args.scenario)
    else:
        result = cloud_run(args)
    print(json.dumps({key: result.get(key) for key in ("run_id", "status", "simulation", "reason")}
                     | {"decision": result.get("evaluation", {}).get("decision")}, indent=2))


if __name__ == "__main__":
    main()
