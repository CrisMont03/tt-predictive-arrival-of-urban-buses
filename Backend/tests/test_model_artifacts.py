"""Contract checks run without ML dependencies; real Keras roundtrip is optional."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.model_inputs import FallbackAccumulator, contract, fit_scaler, online_inputs
from app.predictor import Predictor

trainer_path = Path(__file__).resolve().parents[2] / "Modelo" / "entrenar.py"
spec = importlib.util.spec_from_file_location("arribo_trainer", trainer_path)
trainer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trainer)


def metadata():
    inputs = online_inputs({"temp_c": 20}, 10, now="2026-10-20T14:00:00Z")
    scaler = fit_scaler([dict(sample_id="test-only", inputs=inputs, split="train", target=10, loss_weight=1)])
    fallback = FallbackAccumulator()
    fallback.add("rio_consulado", "2026-10-19T14:00:00Z", 10)
    return dict(schema_version="arribo-sequences-v2", model_version="unit-only", window=10,
                contract=contract(10), scaler=scaler, fallback=fallback.table, radius=5,
                model_sha256=hashlib.sha256(b"unit-only-not-a-real-model").hexdigest(),
                eligible=True, approved=True, serving_bus_context="always_missing; no live camera")


def save(tmp_path, value):
    (tmp_path / "metadata.json").write_text(json.dumps(value))
    (tmp_path / "model.keras").write_bytes(b"unit-only-not-a-real-model")


@pytest.mark.parametrize("change,error", [
    ({"schema_version": "arribo-sequences-v1"}, "aprobado"),
    ({"approved": False}, "aprobado"),
    ({"eligible": False}, "aprobado"),
    ({"contract": {}}, "contrato"),
    ({"fallback": {}}, "respaldos"),
    ({"radius": float("inf")}, "Intervalo"),
    ({"model_sha256": "wrong"}, "checksum"),
])
def test_reject_incompatible_or_unapproved_artifacts_before_tensorflow(tmp_path, change, error):
    save(tmp_path, {**metadata(), **change})
    with pytest.raises(ValueError, match=error):
        Predictor(tmp_path)


def test_reject_old_model_input_shape(monkeypatch, tmp_path):
    save(tmp_path, metadata())
    fake = SimpleNamespace(inputs=[SimpleNamespace(name="sequence", shape=(None, 10, 24))], output_shape=(None, 1))
    monkeypatch.setitem(__import__("sys").modules, "tensorflow", SimpleNamespace(keras=SimpleNamespace(models=SimpleNamespace(load_model=lambda *a, **k: fake))))
    with pytest.raises(ValueError, match="esquema"):
        Predictor(tmp_path)


def test_trainer_rejects_old_preparation_before_importing_ml(tmp_path):
    (tmp_path / "quality.json").write_text(json.dumps({"schema_version": "arribo-sequences-v1"}))
    with pytest.raises(ValueError, match="incompatible"):
        trainer.load_preparation(tmp_path)


def test_real_keras_mask_and_save_load_roundtrip_when_ml_is_installed(tmp_path):
    tf = pytest.importorskip("tensorflow", reason="Instalar Modelo/requirements.txt para probar Keras real")
    np = pytest.importorskip("numpy")
    model = trainer.build_lstm(10)
    sequence = np.zeros((1, 10, 27), dtype="float32")
    mask = np.array([[False] * 9 + [True]])
    first = model({"sequence": sequence, "step_mask": mask}, training=False).numpy()
    noisy = sequence.copy()
    noisy[:, :9] = 100
    assert np.allclose(first, model({"sequence": noisy, "step_mask": mask}, training=False).numpy())
    model.save(tmp_path / "model.keras")
    value = metadata()
    value["model_sha256"] = hashlib.sha256((tmp_path / "model.keras").read_bytes()).hexdigest()
    (tmp_path / "metadata.json").write_text(json.dumps(value))
    loaded = Predictor(tmp_path)
    wait, interval = loaded.predict({}, now="2026-10-20T14:00:00Z")
    assert 0 <= wait <= 45 and interval[0] <= interval[1]
