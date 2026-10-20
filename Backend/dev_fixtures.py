"""Fixtures C.3–C.5. Preview is offline; cloud writes require explicit confirmation."""
import argparse
import json
import math
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_UID = "0Qv94FxE5KZLJ0eRuB6WG4BV7Q93"
FIXTURE_TAG = "arribo-c345-v1"
CASES = ("online", "offline", "retry", "long", "expired")


def fixture_documents(uid, run_id, now=None, extra_queries=0):
    if not uid or len(uid) > 128 or "/" in uid:
        raise ValueError("UID inválido")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,23}", run_id):
        raise ValueError("run-id: 1–24 letras minúsculas, números o guiones")
    if not 0 <= extra_queries <= 25:
        raise ValueError("extra-queries debe estar entre 0 y 25")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("La fecha debe tener zona horaria")
    prefix = f"arribo-c345-{run_id}"
    metadata = {"fixture_tag": FIXTURE_TAG, "fixture_run": run_id, "fixture_uid": uid}
    documents = {}
    cases = list(CASES) + [f"page-{i:02d}" for i in range(extra_queries)]
    for index, case in enumerate(cases):
        query_id = f"{prefix}-{case}"
        requested = now - timedelta(minutes=index, seconds=30)
        local = requested.astimezone(ZoneInfo("America/Mexico_City"))
        documents[f"query_history/{uid}/queries/{query_id}"] = {
            **metadata, "query_id": query_id, "station_id": "rio_consulado",
            "waiting_time_min": 12.0, "confidence_interval": [8.0, 17.0],
            "model_version": "test-fixture-c345", "timestamp": requested,
            "requested_at": requested,
            "expires_at": now - timedelta(minutes=1) if case == "expired" else requested + timedelta(minutes=5),
            "source": "mock", "simulation": True,
            "context": {"hour": local.hour, "day_of_week": local.weekday(),
                        "is_weekend": int(local.weekday() >= 5), "is_holiday": 0,
                        "precipitation_mm": 0.0, "temp_c": 19.0, "humidity": 65.0,
                        "traffic_density": None, "env_alert_active": 0},
        }
    for case, severity, active, expired in [
        ("info", "info", True, False), ("warning", "warning", True, False),
        ("critical", "critical", True, False), ("inactive", "info", False, False),
        ("expired", "warning", True, True),
    ]:
        documents[f"alerts/{prefix}-{case}"] = {
            **metadata, "station_id": "rio_consulado", "type": "prueba_integracion",
            "message": f"PRUEBA C.3–C.5 [{run_id}] — {case}. No describe el servicio real.",
            "severity": severity, "active": active, "simulation": True,
            "created_at": now - timedelta(minutes=10),
            "expires_at": now + timedelta(hours=4) if not expired else now - timedelta(minutes=1),
        }
    return documents


def owned_fixture(data, expected):
    return bool(data) and all(data.get(key) == expected[key] for key in (
        "fixture_tag", "fixture_run", "fixture_uid", "station_id", "simulation"
    ))


def seed_documents(db, documents):
    """Validate all collisions first. Atomic create-only batch, never set/overwrite."""
    batch = db.batch()
    created, preserved = [], []
    for path, expected in documents.items():
        reference = db.document(path)
        snapshot = reference.get()
        if snapshot.exists:
            if not owned_fixture(snapshot.to_dict(), expected):
                raise ValueError(f"Colisión con un documento no perteneciente a esta prueba: {path}")
            preserved.append(path)
        else:
            batch.create(reference, expected)
            created.append(path)
    if created:
        batch.commit()
    return {"created": created, "preserved_without_changes": preserved}


def retire_alerts(db, documents):
    batch = db.batch()
    retired = []
    for path, expected in documents.items():
        if not path.startswith("alerts/"):
            continue
        reference = db.document(path)
        snapshot = reference.get()
        if not snapshot.exists:
            continue
        if not owned_fixture(snapshot.to_dict(), expected):
            raise ValueError(f"No se modificará una alerta ajena a esta prueba: {path}")
        batch.update(reference, {"active": False})
        retired.append(path)
    if retired:
        batch.commit()
    return {"retired": retired, "deleted": 0}


def inspect_documents(db, documents, required_feedback=()):
    queries, alerts, failures = [], [], []
    for path, expected in documents.items():
        snapshot = db.document(path).get()
        data = snapshot.to_dict() if snapshot.exists else None
        if not owned_fixture(data, expected):
            failures.append(f"Falta la fixture o no coincide su identidad: {path}")
            continue
        if path.startswith("alerts/"):
            alerts.append({"id": snapshot.id, "active": data["active"],
                           "expired": data["expires_at"] <= datetime.now(timezone.utc)})
            continue
        query_id = expected["query_id"]
        uid = expected["fixture_uid"]
        report_path = f"feedback/{uid}/entries/{query_id}"
        report = db.document(report_path).get().to_dict()
        case = query_id.removeprefix(f"arribo-c345-{expected['fixture_run']}-")
        queries.append({"case": case, "query_id": query_id,
                        "actual_min": data.get("actual_min"), "feedback_exists": report is not None})
        if report is None:
            if case in required_feedback:
                failures.append(f"No se recibió el feedback esperado: {report_path}")
            continue
        actual = report.get("actual_min")
        valid_actual = type(actual) in (int, float) and math.isfinite(actual) and 0 <= actual <= 180
        if not valid_actual or any([
            report.get("uid") != uid, report.get("query_id") != query_id,
            report.get("station_id") != data["station_id"], report.get("simulation") is not True,
            report.get("predicted_min") != data["waiting_time_min"],
            report.get("context") != data["context"], report.get("query_at") != data["requested_at"],
            report.get("model_version") != data["model_version"], data.get("actual_min") != actual,
        ]):
            failures.append(f"Feedback o historial inconsistente: {report_path}")
        else:
            error = report.get("error_min")
            valid_error = type(error) in (int, float) and math.isfinite(error)
            if not valid_error or not math.isclose(error, actual - data["waiting_time_min"]):
                failures.append(f"Error calculado incorrectamente: {report_path}")
            if report.get("outside_domain") is not (actual > 45):
                failures.append(f"Dominio de espera incorrecto: {report_path}")
            received = report.get("received_at")
            if not isinstance(received, datetime) or received.tzinfo is None:
                failures.append(f"Falta la fecha de recepción del servidor: {report_path}")
    return {"queries": queries, "alerts": alerts, "failures": failures}


def connect_development(project, uid, confirmation=None, writing=False):
    if not project or not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project):
        raise ValueError("Indica --project con el ID del proyecto de desarrollo")
    if project.startswith("demo-"):
        raise ValueError("Esta CLI es para Firebase real de desarrollo; usa test:integration para emuladores")
    if any(os.getenv(key) for key in ("FIRESTORE_EMULATOR_HOST", "FIREBASE_AUTH_EMULATOR_HOST")):
        raise ValueError("No mezcles credenciales/proyecto real con variables de emuladores")
    if writing and confirmation != project:
        raise ValueError("Para escribir, --confirm-dev-project debe coincidir con --project")
    import firebase_admin
    from firebase_admin import auth, firestore
    app = firebase_admin.initialize_app(options={"projectId": project}, name="arribo-c345-admin")
    user = auth.get_user(uid, app=app)
    if user.disabled:
        raise ValueError("La cuenta de prueba está deshabilitada")
    return firestore.client(app=app)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["preview", "seed", "verify", "retire-alerts"])
    parser.add_argument("--uid", default=DEFAULT_UID)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--extra-queries", type=int, default=0)
    parser.add_argument("--project")
    parser.add_argument("--confirm-dev-project")
    parser.add_argument("--apply", action="store_true", help="Autoriza escritura remota del escenario especificado")
    parser.add_argument("--require-feedback", nargs="*", choices=CASES, default=[])
    args = parser.parse_args()
    try:
        documents = fixture_documents(args.uid, args.run_id, extra_queries=args.extra_queries)
        if args.action == "preview" or args.action in {"seed", "retire-alerts"} and not args.apply:
            print(json.dumps({"dry_run": True, "uid": args.uid, "project": args.project,
                              "warning": "Las alertas son visibles para todos los usuarios autenticados del proyecto de desarrollo.",
                              "documents": documents}, default=str, ensure_ascii=False, indent=2))
            return 0
        db = connect_development(args.project, args.uid, args.confirm_dev_project,
                                 writing=args.action != "verify")
        if args.action == "seed":
            result = seed_documents(db, documents)
        elif args.action == "retire-alerts":
            result = retire_alerts(db, documents)
        else:
            result = inspect_documents(db, documents, args.require_feedback)
        print(json.dumps(result, default=str, ensure_ascii=False, indent=2))
        return 1 if result.get("failures") else 0
    except Exception as error:
        # Never print tokens, credentials or arbitrary cloud response payloads.
        if isinstance(error, ValueError):
            parser.error(str(error))
        print(f"Operación no completada ({type(error).__name__}). Revisa proyecto, ADC/IAM y conectividad.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
