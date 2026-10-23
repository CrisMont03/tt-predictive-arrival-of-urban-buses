"""Deployment authority. Dry by default; --execute and project confirmation are required."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Backend")]
from app.artifacts import digest, download_package, immutable_json, json_bytes, publish_package, read_json
from app.predictor import Predictor
from Modelo.operacion.evaluation import compare
from Modelo.operacion.promotion import CloudRunDriver, promote, rollback
from Modelo.operacion.registry import GCSRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-dev-project")
    parser.add_argument("--run-id")
    parser.add_argument("--manual-initial", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--register-only", action="store_true", help="Registrar candidato inicial local; no desplegar")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--benchmark", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print("INSPECCIÓN: no se descargan ni publican modelos; no se modifica Cloud Run.")
        return
    if args.confirm_dev_project != "arribo-68326" or os.getenv("GOOGLE_CLOUD_PROJECT") != "arribo-68326":
        parser.error("Confirma el proyecto de desarrollo y GOOGLE_CLOUD_PROJECT")
    registry = GCSRegistry(os.environ["ARRIBO_REGISTRY_URI"])
    if args.register_only:
        if not args.run_id or not args.candidate or not args.benchmark:
            parser.error("Registrar exige --run-id, --candidate y --benchmark")
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", args.run_id):
            parser.error("Identificador de corrida inválido")
        candidate = Predictor(args.candidate, require_approved=False)
        current, _ = registry.read("deployment/current")
        incumbent = None
        if current:
            ref = current["package"]
            path, _ = download_package(ref["uri"], ref["generation"], ref["sha256"])
            incumbent = Predictor(path)
        benchmark = read_json(args.benchmark)
        evaluation = compare(benchmark, candidate, incumbent)
        package = publish_package(args.candidate, os.environ["ARRIBO_CANDIDATES_URI"], args.run_id + "-candidate")
        registry.cas("runs/" + args.run_id, dict(run_id=args.run_id, status="evaluated", simulation=False,
                     origin="manual_registration", evaluation=evaluation, candidate_package=package,
                     dataset_sha256=candidate.metadata["dataset_sha256"]), 0)
        print(json.dumps({"status": "registered_not_deployed", "run_id": args.run_id}))
        return
    driver = CloudRunDriver("arribo-68326", os.getenv("ARRIBO_REGION", "us-central1"),
                            os.getenv("ARRIBO_SERVICE", "arribo-api"), os.environ["ARRIBO_API_IMAGE"])
    if args.rollback:
        print(json.dumps(rollback(registry, driver, enabled=True)))
        return
    suspended, _ = registry.read("promotion/suspended")
    if suspended and suspended.get("suspended"):
        parser.error("Promociones suspendidas tras rollback; revisar antes de reactivarlas")
    if not args.run_id and os.getenv("ARRIBO_AUTO_PROMOTION") != "true":
        parser.error("La promoción automática está desactivada")
    latest, _ = registry.read("latest_evaluated")
    run_id = args.run_id or (latest or {}).get("run_id")
    record, _ = registry.read("runs/" + (run_id or "missing"))
    if not record or record.get("status") != "evaluated" or record.get("simulation") is not False:
        parser.error("Se requiere una corrida real evaluada")
    current, _ = registry.read("deployment/current")
    if current and current["model_version"] == record["evaluation"]["candidate_version"]:
        deployment, _ = registry.read("deployments/" + current["package"]["release_id"])
        if deployment and deployment.get("status") == "switching":
            result = promote(registry, driver, current["package"], record["evaluation"],
                             expected_incumbent=record["evaluation"]["incumbent_version"],
                             model_version=current["model_version"], manual_initial=args.manual_initial, enabled=True)
            print(json.dumps({"status": result["status"]}))
            return
        print(json.dumps({"status": "already_active"}))
        return
    ref = record["candidate_package"]
    candidate_path, _ = download_package(ref["uri"], ref["generation"], ref["sha256"])
    candidate = Predictor(candidate_path, require_approved=False)
    incumbent = None
    if current:
        old = current["package"]
        path, _ = download_package(old["uri"], old["generation"], old["sha256"])
        incumbent = Predictor(path)
    # Recompute with the actual current model, not just a stale report supplied by training.
    evaluation = compare(record["evaluation"]["benchmark"], candidate, incumbent)
    if not evaluation["publishable"] and not (args.manual_initial and evaluation["decision"] == "manual_initial_approval"):
        print(json.dumps({"status": "kept", "decision": evaluation["decision"]}))
        return
    approved = Path(tempfile.mkdtemp(prefix="arribo-approved-"))
    shutil.copyfile(candidate_path / "model.keras", approved / "model.keras")
    metadata = {**candidate.metadata, "approved": True, "approval_evaluation_sha256": digest(json_bytes(evaluation))}
    immutable_json(approved / "metadata.json", metadata)
    release = candidate.metadata["model_version"] + "-approved"
    # Loading the approved copy validates Keras and the unchanged weights before upload.
    Predictor(approved)
    package = publish_package(approved, os.environ["ARRIBO_RELEASES_URI"], release)
    result = promote(registry, driver, package, evaluation,
                     expected_incumbent=incumbent.metadata["model_version"] if incumbent else None,
                     model_version=candidate.metadata["model_version"], manual_initial=args.manual_initial, enabled=True)
    print(json.dumps({"status": result["status"], "model_version": result["model_version"]}))


if __name__ == "__main__":
    main()
