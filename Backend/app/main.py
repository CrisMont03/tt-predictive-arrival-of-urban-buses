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
from .store import FeedbackConflict, FeedbackTemporarilyUnavailable, FirestoreStore, MemoryStore
from .context import cached_sources_valid, fetch_context
from .predictor import Predictor
from .model_inputs import contract, online_inputs, select_observations


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
        context_sources = None
        if mode == "demo":
            external = dict(precipitation_mm=0.0, temp_c=19.0, humidity=65.0, traffic_density=.72, env_alert_active=0)
        else:
            external = await fetch_context()
            context_sources = external.pop("_sources", None)
            observations = select_observations(
                store.recent_observations(body.station_id, now - timedelta(hours=2)),
                now, body.station_id, predictor.metadata.get("window", 10))
        context = Context(hour=local.hour, day_of_week=local.weekday(), is_weekend=int(local.weekday() >= 5), is_holiday=int(local.date() in holidays.Mexico(years=local.year)), **external)
        window = 10 if mode == "demo" else predictor.metadata.get("window", 10)
        prepared = online_inputs(context.model_dump(), window, now=now, station_id=body.station_id,
                                 observations=observations,
                                 fallback=None if mode == "demo" else predictor.metadata.get("fallback"))
        revision = hashlib.sha256(json.dumps({
            "context": context.model_dump(), "contract": contract(window),
            "inputs": {key: value for key, value in prepared.items() if key != "provenance"},
            "history": [(item.get("uid"), item["query_id"], item["actual_min"], item["received_at"].isoformat()) for item in observations]
        }, sort_keys=True, allow_nan=False).encode()).hexdigest()
        key = hashlib.sha256(f"{body.station_id}|{local.date()}|{local.hour}|{version}|{revision}".encode()).hexdigest()
        cached = store.get_cache(key)
        if cached and cached["expires_at"] > now and cached_sources_valid(cached.get("context_sources"), datetime.now(timezone.utc)):
            value = {**cached, "source": "cache", "query_id": body.request_id}
        else:
            wait, interval = (12.3, (8, 17)) if mode == "demo" else predictor.predict(
                context.model_dump(), observations, now=now, station_id=body.station_id)
            value = PredictResponse(query_id=body.request_id, station_id=body.station_id, waiting_time_min=wait, confidence_interval=interval, model_version=version, timestamp=now, requested_at=now, expires_at=now + timedelta(minutes=5), source="mock" if mode == "demo" else "model", simulation=mode == "demo", context=context, context_sources=context_sources).model_dump()
            store.set_cache(key, value)
        return store.create_query(uid, body.request_id, {**value, "requested_at": now})

    @app.post("/feedback")
    def feedback(body: FeedbackRequest, uid=Depends(user)):
        original = store.get_query(uid, body.query_id)
        if not original:
            raise HTTPException(404, {"code": "QUERY_NOT_FOUND", "message": "No se encontró esta consulta en tu cuenta."})
        report = {"query_id": body.query_id, "uid": uid, "station_id": original["station_id"], "actual_min": body.actual_min, "predicted_min": original["waiting_time_min"], "error_min": body.actual_min - original["waiting_time_min"], "context": original["context"], "query_at": original.get("requested_at", original["timestamp"]), "received_at": datetime.now(timezone.utc), "simulation": original["simulation"], "outside_domain": body.actual_min > 45, "model_version": original["model_version"]}
        if original.get("context_sources") is not None:
            report["context_sources"] = original["context_sources"]
        try:
            saved = store.save_feedback(uid, body.query_id, report)
        except FeedbackConflict as error:
            raise HTTPException(409, {"code": "FEEDBACK_EXISTS", "message": str(error)})
        except FeedbackTemporarilyUnavailable:
            raise HTTPException(503, {"code": "SERVICE_ERROR", "message": "No se pudo confirmar el reporte. Reintenta con la misma consulta y espera."})
        return {"status": "received", "query_id": body.query_id, "simulation": saved["simulation"], "outside_domain": saved["outside_domain"]}
    return app
