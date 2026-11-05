import hashlib
import json
from math import isfinite
from pathlib import Path
from .initial_approval import valid as initial_exception_valid

from .model_inputs import INPUT_WIDTH, SCHEMA_VERSION, FEEDBACK_SCHEMA, NEXT_BUS_SCHEMA, NEXT_BUS_MAX_WAIT, SUPPORTED_SCHEMAS, contract, online_inputs, transform


class Predictor:
    def __init__(self, directory, *, require_approved=True):
        path = Path(directory)
        self.metadata = json.loads((path / "metadata.json").read_text())
        if (self.metadata.get("schema_version") not in SUPPORTED_SCHEMAS
                or (require_approved and (self.metadata.get("simulation")
                    or self.metadata.get("approved") is not True
                    or (self.metadata.get("eligible") is not True and not initial_exception_valid(self.metadata))))):
            raise ValueError("El artefacto no está validado/aprobado para producción")
        self.kind = self.metadata.get("predictor_kind", "lstm")
        self.max_wait = NEXT_BUS_MAX_WAIT if self.metadata["schema_version"] == NEXT_BUS_SCHEMA else 45
        if self.metadata["schema_version"] == NEXT_BUS_SCHEMA and self.metadata.get("target_definition") != "next_bus_wait":
            raise ValueError("El paquete carece del objetivo próximo bus")
        window = self.metadata["window"]
        if self.metadata.get("contract") != contract(window, self.metadata["schema_version"]):
            raise ValueError("El contrato de preparación no coincide")
        policy = "feedback-derived-arrivals-v1" if self.metadata["schema_version"] in (FEEDBACK_SCHEMA, NEXT_BUS_SCHEMA) else "always_missing; no live camera"
        if not self.metadata.get("fallback") or self.metadata.get("serving_bus_context") != policy:
            raise ValueError("El artefacto requiere respaldos y operación sin cámara")
        radius = self.metadata["radius"]
        if not isinstance(radius, (int, float)) or not isfinite(radius) or radius < 0:
            raise ValueError("Intervalo inválido")
        # Validate scaler before loading TensorFlow or accepting any request.
        transform(online_inputs({}, window, now="2026-01-01T12:00:00Z", schema_version=self.metadata["schema_version"]), self.metadata["scaler"])
        if self.kind == "historical":
            if self.metadata["schema_version"] != NEXT_BUS_SCHEMA:
                raise ValueError("Referencia histórica con contrato incompatible")
            raw = (path / "historical.json").read_bytes()
            if hashlib.sha256(raw).hexdigest() != self.metadata["historical_sha256"]:
                raise ValueError("Checksum histórico incorrecto")
            if json.loads(raw) != {"fallback": self.metadata["fallback"], "radius": radius}:
                raise ValueError("Respaldos históricos incompatibles")
            self.model = None
            return
        if self.kind != "lstm":
            raise ValueError("Predictor desconocido")
        if hashlib.sha256((path / "model.keras").read_bytes()).hexdigest() != self.metadata["model_sha256"]:
            raise ValueError("El checksum del modelo no coincide")
        import tensorflow as tf
        self.model = tf.keras.models.load_model(path / "model.keras", compile=False, safe_mode=True)
        shapes = {value.name.split(":")[0]: tuple(value.shape[1:]) for value in self.model.inputs}
        if shapes != {"sequence": (window, INPUT_WIDTH), "step_mask": (window,)} or tuple(self.model.output_shape[1:]) != (1,):
            raise ValueError("El esquema del modelo no coincide")

    def prepare(self, context, observations=None, *, now, station_id="rio_consulado"):
        return online_inputs(context, self.metadata["window"], now=now, station_id=station_id,
                             observations=observations, fallback=self.metadata["fallback"],
                             schema_version=self.metadata["schema_version"])

    def predict(self, context, observations=None, *, now, station_id="rio_consulado"):
        return self.predict_prepared(self.prepare(context, observations, now=now, station_id=station_id))

    def predict_prepared(self, inputs):
        if self.kind == "historical":
            from .model_inputs import fallback_value
            current = inputs["provenance"][-1]["query_at"]
            value = fallback_value(self.metadata["fallback"], self.metadata.get("station_id", "rio_consulado"), current, max_wait=self.max_wait)
            if value is None:
                raise ValueError("No hay referencia histórica disponible")
            radius = self.metadata["radius"]
            return value, (max(0, value-radius), value+radius)
        import numpy as np
        value = float(self.model({"sequence": np.array([transform(inputs, self.metadata["scaler"])], dtype="float32"),
                                 "step_mask": np.array([[bool(v[0]) for v in inputs["step_mask"]]], dtype="bool")},
                                training=False).numpy().reshape(-1)[0])
        if not np.isfinite(value):
            raise ValueError("El modelo devolvió un resultado no finito")
        if self.metadata["schema_version"] == NEXT_BUS_SCHEMA and value > self.max_wait:
            raise ValueError("La predicción excede el dominio de jornada")
        value = max(0, value) if self.metadata["schema_version"] == NEXT_BUS_SCHEMA else max(0, min(45, value))
        radius = self.metadata["radius"]
        return value, (max(0, value - radius), value + radius)
