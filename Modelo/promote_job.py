"""Deployment authority. Dry by default; --execute and project confirmation are required."""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Backend")]
from app.artifacts import digest, download_package, immutable_json, json_bytes, publish_package, read_json
from app.predictor import Predictor
from app.initial_approval import issue as issue_exception, evaluation as exception_evaluation
from Modelo.operacion.evaluation import compare
from Modelo.operacion.promotion import CloudRunDriver, promote, rollback
from Modelo.operacion.registry import GCSRegistry


def release_identifier(model_version):
    """Keep model identity intact while producing an unambiguous Storage release ID."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "-", model_version)[:50]
    return slug + "-" + digest(json_bytes(model_version))[:10] + "-approved"


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
    parser.add_argument("--initial-exception", action="store_true")
    parser.add_argument("--approval-operator")
    parser.add_argument("--approval-reason")
    parser.add_argument("--preparation", type=Path)
    parser.add_argument("--gcloud-session", action="store_true", help="Solo operador local: sesión gcloud, sin guardar tokens")
    parser.add_argument("--resume-promotions", action="store_true", help="Operador: levantar suspensión tras revisar un rollback")
    args = parser.parse_args()
    if not args.execute:
        print("INSPECCIÓN: no se descargan ni publican modelos; no se modifica Cloud Run.")
        return
    if args.confirm_dev_project != "arribo-68326" or os.getenv("GOOGLE_CLOUD_PROJECT") != "arribo-68326":
        parser.error("Confirma el proyecto de desarrollo y GOOGLE_CLOUD_PROJECT")
    credentials = None
    if args.gcloud_session:
        if os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"):
            parser.error("Cloud Run debe usar su identidad asociada, no una sesión local")
        from Infra.cloud_session import credentials as session_credentials
        credentials = session_credentials()
    from google.cloud import storage
    client = storage.Client(project="arribo-68326", credentials=credentials)
    registry = GCSRegistry(os.environ["ARRIBO_REGISTRY_URI"], client=client)
    if args.register_only:
        if not args.run_id or not args.candidate or (not args.benchmark and not args.initial_exception):
            parser.error("Registrar exige --run-id, --candidate y --benchmark")
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,60}", args.run_id):
            parser.error("Identificador de corrida inválido")
        candidate = Predictor(args.candidate, require_approved=False)
        prior, _ = registry.read("runs/" + args.run_id)
        if prior:
            if prior.get("original_metadata_sha256") != digest((args.candidate / "metadata.json").read_bytes()):
                parser.error("La corrida existente corresponde a otro candidato")
            print(json.dumps({"status": "already_registered", "run_id": args.run_id}))
            return
        current, _ = registry.read("deployment/current")
        incumbent = None
        if current:
            ref = current["package"]
            path, _ = download_package(ref["uri"], ref["generation"], ref["sha256"], client=client)
            incumbent = Predictor(path)
        directory = args.candidate
        if args.initial_exception:
            if current or not args.approval_operator or not args.approval_reason or not args.preparation:
                parser.error("Excepción solo inicial: requiere operador, motivo y preparación")
            preparation = read_json(args.preparation / "preparation.json")
            if digest((args.preparation / "preparation.json").read_bytes()) != candidate.metadata["preparation_sha256"]:
                parser.error("La preparación no corresponde al candidato")
            directory = Path(tempfile.mkdtemp(prefix="arribo-initial-"))
            shutil.copyfile(args.candidate / "model.keras", directory / "model.keras")
            candidate.metadata["dataset_sha256"] = preparation["source_sha256"]
            immutable_json(directory / "metadata.json", candidate.metadata)
            grant = issue_exception(candidate.metadata, operator=args.approval_operator, reason=args.approval_reason,
                                    source_sha256=digest((args.candidate / "metadata.json").read_bytes()))
            evaluation = exception_evaluation(candidate.metadata, grant)
        else:
            benchmark = read_json(args.benchmark)
            evaluation = compare(benchmark, candidate, incumbent)
        package = publish_package(directory, os.environ["ARRIBO_CANDIDATES_URI"], args.run_id + "-candidate", client=client)
        registry.cas("runs/" + args.run_id, dict(run_id=args.run_id, status="evaluated", simulation=False,
                     origin="manual_registration", evaluation=evaluation, candidate_package=package,
                     original_metadata_sha256=digest((args.candidate / "metadata.json").read_bytes()),
                     dataset_sha256=candidate.metadata["dataset_sha256"]), 0)
        print(json.dumps({"status": "registered_not_deployed", "run_id": args.run_id}))
        return
    driver = CloudRunDriver("arribo-68326", os.getenv("ARRIBO_REGION", "us-central1"),
                            os.getenv("ARRIBO_SERVICE", "arribo-api"), os.environ["ARRIBO_API_IMAGE"], credentials=credentials)
    if args.rollback:
        print(json.dumps(rollback(registry, driver, enabled=True)))
        return
    if args.resume_promotions:
        if not args.approval_operator or not args.approval_reason:
            parser.error("Reanudar exige operador y motivo de revisión")
        previous, generation = registry.read("promotion/suspended")
        registry.cas("promotion/suspended", {"suspended": False, "operator": args.approval_operator,
                     "reason": args.approval_reason, "previous": previous}, generation)
        print(json.dumps({"status": "resumed"}))
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
    candidate_path, _ = download_package(ref["uri"], ref["generation"], ref["sha256"], client=client)
    candidate = Predictor(candidate_path, require_approved=False)
    incumbent = None
    if current:
        old = current["package"]
        path, _ = download_package(old["uri"], old["generation"], old["sha256"], client=client)
        incumbent = Predictor(path)
    # Recompute with the actual current model, not just a stale report supplied by training.
    if record["evaluation"].get("initial_release_exception"):
        if incumbent or not args.manual_initial:
            parser.error("La excepción exige primera publicación manual, sin vigente")
        evaluation = exception_evaluation(candidate.metadata, record["evaluation"]["initial_release_exception"])
    else:
        evaluation = compare(record["evaluation"]["benchmark"], candidate, incumbent)
    if not evaluation["publishable"] and not (args.manual_initial and evaluation["decision"] == "manual_initial_approval"):
        print(json.dumps({"status": "kept", "decision": evaluation["decision"]}))
        return
    approved = Path(tempfile.mkdtemp(prefix="arribo-approved-"))
    shutil.copyfile(candidate_path / "model.keras", approved / "model.keras")
    metadata = {**candidate.metadata, "approved": True, "approval_evaluation_sha256": digest(json_bytes(evaluation))}
    if evaluation.get("initial_release_exception"):
        metadata.pop("approval_evaluation_sha256")
        metadata["initial_release_exception"] = evaluation["initial_release_exception"]
    immutable_json(approved / "metadata.json", metadata)
    release = release_identifier(candidate.metadata["model_version"])
    # Loading the approved copy validates Keras and the unchanged weights before upload.
    Predictor(approved)
    package = publish_package(approved, os.environ["ARRIBO_RELEASES_URI"], release, client=client)
    result = promote(registry, driver, package, evaluation,
                     expected_incumbent=incumbent.metadata["model_version"] if incumbent else None,
                     model_version=candidate.metadata["model_version"], manual_initial=args.manual_initial, enabled=True)
    print(json.dumps({"status": result["status"], "model_version": result["model_version"]}))


if __name__ == "__main__":
    main()
