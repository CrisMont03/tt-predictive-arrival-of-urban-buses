import hashlib
import json
from math import isfinite
from pathlib import Path

from .model_inputs import INPUT_WIDTH, SCHEMA_VERSION, contract, online_inputs, transform


class Predictor:
    def __init__(self, directory, *, require_approved=True):
        path = Path(directory)
        self.metadata = json.loads((path / "metadata.json").read_text())
        if (self.metadata.get("schema_version") != SCHEMA_VERSION
                or (require_approved and (self.metadata.get("simulation") or self.metadata.get("eligible") is not True
                                         or self.metadata.get("approved") is not True))):
            raise ValueError("El artefacto no está validado/aprobado para producción")
        window = self.metadata["window"]
        if self.metadata.get("contract") != contract(window):
            raise ValueError("El contrato de preparación no coincide")
        if not self.metadata.get("fallback") or self.metadata.get("serving_bus_context") != "always_missing; no live camera":
            raise ValueError("El artefacto requiere respaldos y operación sin cámara")
        radius = self.metadata["radius"]
        if not isinstance(radius, (int, float)) or not isfinite(radius) or radius < 0:
            raise ValueError("Intervalo inválido")
        # Validate scaler before loading TensorFlow or accepting any request.
        transform(online_inputs({}, window, now="2026-01-01T12:00:00Z"), self.metadata["scaler"])
        if hashlib.sha256((path / "model.keras").read_bytes()).hexdigest() != self.metadata["model_sha256"]:
            raise ValueError("El checksum del modelo no coincide")
        import tensorflow as tf
        self.model = tf.keras.models.load_model(path / "model.keras", compile=False, safe_mode=True)
        shapes = {value.name.split(":")[0]: tuple(value.shape[1:]) for value in self.model.inputs}
        if shapes != {"sequence": (window, INPUT_WIDTH), "step_mask": (window,)} or tuple(self.model.output_shape[1:]) != (1,):
            raise ValueError("El esquema del modelo no coincide")

    def predict(self, context, observations=None, *, now, station_id="rio_consulado"):
        import numpy as np
        inputs = online_inputs(context, self.metadata["window"], now=now, station_id=station_id,
                               observations=observations, fallback=self.metadata["fallback"])
        value = float(self.model({"sequence": np.array([transform(inputs, self.metadata["scaler"])], dtype="float32"),
                                 "step_mask": np.array([[bool(v[0]) for v in inputs["step_mask"]]], dtype="bool")},
                                training=False).numpy().reshape(-1)[0])
        if not np.isfinite(value):
            raise ValueError("El modelo devolvió un resultado no finito")
        value = max(0, min(45, value))
        radius = self.metadata["radius"]
        return value, (max(0, value - radius), value + radius)
