"""Train candidates only after chronological splits exist. Never auto-deploy."""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Backend"))
from app.model_inputs import fit_scaler, transform


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Directory from preparar_modelo.py")
    parser.add_argument("--output", type=Path, required=True, help="New immutable candidate directory")
    parser.add_argument("--version", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    args = parser.parse_args()
    quality = json.loads((args.data / "quality.json").read_text())
    if not quality["can_train"]:
        parser.error("Sigue recopilando: se necesitan jornadas distintas para train/validation/test")
    samples = [json.loads(line) for line in (args.data / "sequences.jsonl").read_text().splitlines()]
    partitions = {name: [sample for sample in samples if sample["split"] == name] for name in ("train", "validation", "test")}
    if any(not any(sample["loss_weight"] for sample in rows) for rows in partitions.values()):
        parser.error("Cada partición necesita esperas observadas")
    if args.output.exists():
        parser.error("Usa una carpeta nueva; no se sobreescriben artefactos")
    import numpy as np
    import tensorflow as tf
    from xgboost import XGBRegressor
    tf.keras.utils.set_random_seed(2026050)
    scaler = fit_scaler(partitions["train"])
    window = quality["window"]

    def tensors(rows):
        return np.array([transform(sample["inputs"], scaler) for sample in rows], dtype="float32"), np.array([sample["target"] for sample in rows], dtype="float32"), np.array([sample["loss_weight"] for sample in rows], dtype="float32")

    arrays = {name: tensors(rows) for name, rows in partitions.items()}
    x_train, y_train, weights = arrays["train"]
    # Augment training with the actual initial serving situation: no camera
    # history, no measured bus arrivals and no previous observed wait.
    operational_train = np.zeros_like(x_train)
    operational_train[:, -1, :14] = x_train[:, -1, :14]
    without_buses = x_train.copy(); without_buses[:, :, 16:] = 0
    x_augmented = np.concatenate([x_train, operational_train, without_buses])
    y_augmented = np.concatenate([y_train, y_train, y_train])
    weights_augmented = np.concatenate([weights, weights, weights])
    x_val, y_val, w_val = arrays["validation"]
    model = tf.keras.Sequential([tf.keras.layers.Input(shape=(window, 24)), tf.keras.layers.Masking(mask_value=0), tf.keras.layers.LSTM(32, use_cudnn=False), tf.keras.layers.Dense(16, activation="relu"), tf.keras.layers.Dense(1)])
    model.compile(optimizer="adam", loss="mse")
    model.fit(x_augmented, y_augmented, sample_weight=weights_augmented, validation_data=(x_val, y_val, w_val), epochs=args.epochs, batch_size=32, shuffle=False, callbacks=[tf.keras.callbacks.EarlyStopping(patience=6, restore_best_weights=True)], verbose=2)
    xgb = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=.05, objective="reg:squarederror", random_state=2026050)
    xgb.fit(x_augmented[:, -1], y_augmented, sample_weight=weights_augmented)
    by_hour = defaultdict(list)
    for sample in partitions["train"]:
        if sample["loss_weight"]:
            by_hour[int(sample["inputs"]["temporal"][-1][0])].append(sample["target"])
    all_waits = [value for values in by_hour.values() for value in values]
    historical = {str(hour): sum(values) / len(values) for hour, values in by_hour.items()}
    global_mean = sum(all_waits) / len(all_waits)

    def predict(kind, rows, operational=False):
        x, _, _ = tensors(rows)
        if operational:
            reduced = np.zeros_like(x); reduced[:, -1, :14] = x[:, -1, :14]; x = reduced
        if kind == "lstm":
            result = model.predict(x, verbose=0).reshape(-1)
        elif kind == "xgboost":
            result = xgb.predict(x[:, -1])
        else:
            result = np.array([historical.get(str(int(sample["inputs"]["temporal"][-1][0])), global_mean) for sample in rows])
        return np.clip(result, 0, 45)

    def metrics(rows, predictions, radius):
        observed = np.array([sample["loss_weight"] == 1 for sample in rows])
        y = np.array([sample["target"] for sample in rows])[observed]
        values = predictions[observed]; residual = y - values
        variance = float(np.sum((y - y.mean()) ** 2))
        return {"count": len(y), "mae": float(np.mean(np.abs(residual))), "rmse": float(np.sqrt(np.mean(residual ** 2))), "r2": float(1 - np.sum(residual ** 2) / variance) if variance else None, "interval_coverage": float(np.mean(np.abs(residual) <= radius))}

    report = {}
    radii = {}
    for kind in ("historical", "xgboost", "lstm"):
        observed = np.array([sample["loss_weight"] == 1 for sample in partitions["validation"]])
        validation_residuals = np.abs(y_val[observed] - predict(kind, partitions["validation"], True)[observed])
        radius = float(np.quantile(validation_residuals, .9, method="higher")); radii[kind] = radius
        report[kind] = {name: metrics(rows, predict(kind, rows), radius) for name, rows in partitions.items()}
        report[kind]["test_without_history"] = metrics(partitions["test"], predict(kind, partitions["test"], True), radius)
    operational = report["lstm"]["test_without_history"]
    eligible = operational["mae"] < 5 and operational["rmse"] < 7 and operational["r2"] is not None and operational["r2"] > .7 and operational["interval_coverage"] >= .85
    args.output.mkdir(parents=True)
    model.save(args.output / "model.keras")
    xgb.save_model(args.output / "xgboost.json")
    model_hash = hashlib.sha256((args.output / "model.keras").read_bytes()).hexdigest()
    metadata = {"schema_version": "arribo-sequences-v1", "model_version": args.version, "window": window, "scaler": scaler, "radius": radii["lstm"], "model_sha256": model_hash, "dataset_sha256": hashlib.sha256((args.data / "sequences.jsonl").read_bytes()).hexdigest(), "eligible": eligible, "approved": False, "metrics": report, "historical": historical, "global_mean": global_mean, "quality": quality, "note": "Requires independent operational review before approval. No automatic deployment."}
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    print(f"Candidate saved: {args.output}; eligible={eligible}; approved=False")


if __name__ == "__main__":
    main()
