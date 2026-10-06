import hashlib
import json
from pathlib import Path

from .model_inputs import online_inputs, transform


class Predictor:
    def __init__(self, directory):
        path = Path(directory)
        self.metadata = json.loads((path / "metadata.json").read_text())
        if self.metadata.get("schema_version") != "arribo-sequences-v1" or not self.metadata.get("eligible") or not self.metadata.get("approved"):
            raise ValueError("El artefacto no está validado/aprobado para producción")
        if hashlib.sha256((path / "model.keras").read_bytes()).hexdigest() != self.metadata["model_sha256"]:
            raise ValueError("El checksum del modelo no coincide")
        import tensorflow as tf
        self.model = tf.keras.models.load_model(path / "model.keras", compile=False, safe_mode=True)
        if self.model.input_shape[1:] != (self.metadata["window"], 24):
            raise ValueError("El esquema del modelo no coincide")

    def predict(self, context, observations=None):
        import numpy as np
        inputs = online_inputs(context, self.metadata["window"], observations=observations)
        value = float(self.model(np.array([transform(inputs, self.metadata["scaler"])], dtype="float32"), training=False).numpy().reshape(-1)[0])
        if not np.isfinite(value):
            raise ValueError("El modelo devolvió un resultado no finito")
        value = max(0, min(45, value))
        radius = self.metadata["radius"]
        return value, (max(0, value - radius), value + radius)
