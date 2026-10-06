import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import holidays
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .contracts import Context, FeedbackRequest, PredictRequest, PredictResponse
from .store import FirestoreStore, MemoryStore
from .context import fetch_context
from .predictor import Predictor


def create_app(mode=None, store=None, predictor=None):
    mode = mode or os.getenv("ARRIBO_MODE", "production")
    if mode not in {"demo", "production"}:
        raise ValueError("ARRIBO_MODE debe ser demo o production")
    if store is None:
        if mode == "demo":
            store = MemoryStore()
        else:
            import firebase_admin
            if not firebase_admin._apps:
                firebase_admin.initialize_app()
            store = FirestoreStore()
    if mode == "production" and isinstance(store, MemoryStore):
        raise ValueError("Producción requiere Firestore")
    if mode == "production" and predictor is None and os.getenv("ARRIBO_MODEL_DIR"):
        predictor = Predictor(os.environ["ARRIBO_MODEL_DIR"])
    app = FastAPI(title="ARRIBO", version="1.0.0", description="Predicción de espera; las simulaciones nunca se usan para aprendizaje.")
    app.state.store = store
    app.state.mode = mode
    app.state.predictor = predictor
    origins = [value.strip() for value in os.getenv("ARRIBO_CORS_ORIGINS", "http://localhost:8081,http://localhost:8091").split(",") if value.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        detail = error.detail if isinstance(error.detail, dict) else {"code": "REQUEST_FAILED", "message": str(error.detail)}
        return JSONResponse(detail, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"code": "INVALID_REQUEST", "message": "Revisa los campos de la solicitud."}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        logging.getLogger("arribo").error("Request failed: %s", type(error).__name__)
        return JSONResponse({"code": "SERVICE_ERROR", "message": "No se pudo completar la operación. Inténtalo de nuevo."}, status_code=500)

    def user(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Inicia sesión para continuar."})
        token = authorization[7:]
        if mode == "demo" and token.startswith("demo-") and 5 < len(token) < 100:
            return token
        if mode == "demo":
            raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Usa una sesión de demostración."})
        from firebase_admin import auth
        try:
            return auth.verify_id_token(token, check_revoked=True)["uid"]
        except (ValueError, auth.InvalidIdTokenError, auth.RevokedIdTokenError, auth.UserDisabledError):
            raise HTTPException(401, {"code": "UNAUTHENTICATED", "message": "Tu sesión venció. Inicia sesión de nuevo."})

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": mode, "model_ready": predictor is not None, "simulation": mode == "demo"}

    @app.post("/predict", response_model=PredictResponse)
    async def predict(body: PredictRequest, uid=Depends(user)):
        existing = store.get_query(uid, body.request_id)
        if existing:
            return existing
        if mode != "demo" and predictor is None:
            raise HTTPException(503, {"code": "MODEL_NOT_READY", "message": "El predictor aún no está disponible. Inténtalo más tarde."})
        now = datetime.now(timezone.utc)
        local = now.astimezone(ZoneInfo("America/Mexico_City"))
        version = "demo-v1" if mode == "demo" else predictor.metadata["model_version"]
        observations = []
        if mode == "demo":
            external = dict(precipitation_mm=0.0, temp_c=19.0, humidity=65.0, traffic_density=.72, env_alert_active=0)
        else:
            external = await fetch_context()
            observations = sorted((report for report in store.recent_observations(body.station_id, now - timedelta(hours=2)) if report["received_at"] < now and now - timedelta(hours=2) <= report["query_at"] < now), key=lambda report: report["query_at"])[-20:]
        context = Context(hour=local.hour, day_of_week=local.weekday(), is_weekend=int(local.weekday() >= 5), is_holiday=int(local.date() in holidays.Mexico(years=local.year)), **external)
        revision = hashlib.sha256(json.dumps({"context": context.model_dump(), "history": [(item["query_id"], item["actual_min"]) for item in observations]}, sort_keys=True).encode()).hexdigest()
        key = hashlib.sha256(f"{body.station_id}|{local.date()}|{local.hour}|{version}|{revision}".encode()).hexdigest()
        cached = store.get_cache(key)
        if cached and cached["expires_at"] > now:
            value = {**cached, "source": "cache", "query_id": body.request_id}
        else:
            wait, interval = (12.3, (8, 17)) if mode == "demo" else predictor.predict(context.model_dump(), observations)
            value = PredictResponse(query_id=body.request_id, station_id=body.station_id, waiting_time_min=wait, confidence_interval=interval, model_version=version, timestamp=now, requested_at=now, expires_at=now + timedelta(minutes=5), source="mock" if mode == "demo" else "model", simulation=mode == "demo", context=context).model_dump()
            store.set_cache(key, value)
        return store.create_query(uid, body.request_id, {**value, "requested_at": now})

    @app.post("/feedback")
    def feedback(body: FeedbackRequest, uid=Depends(user)):
        original = store.get_query(uid, body.query_id)
        if not original:
            raise HTTPException(404, {"code": "QUERY_NOT_FOUND", "message": "No se encontró esta consulta en tu cuenta."})
        report = {"query_id": body.query_id, "uid": uid, "station_id": original["station_id"], "actual_min": body.actual_min, "predicted_min": original["waiting_time_min"], "error_min": body.actual_min - original["waiting_time_min"], "context": original["context"], "query_at": original.get("requested_at", original["timestamp"]), "received_at": datetime.now(timezone.utc), "simulation": original["simulation"], "outside_domain": body.actual_min > 45, "model_version": original["model_version"]}
        try:
            saved = store.save_feedback(uid, body.query_id, report)
        except ValueError as error:
            raise HTTPException(409, {"code": "FEEDBACK_EXISTS", "message": str(error)})
        return {"status": "received", "query_id": body.query_id, "simulation": saved["simulation"], "outside_domain": saved["outside_domain"]}
    return app
