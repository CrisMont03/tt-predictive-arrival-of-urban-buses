"""Train immutable candidates using v2 sequences; never approve or deploy."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Backend"))
from app.model_inputs import (
    INPUT_WIDTH, MAX_WAIT, SCHEMA_VERSION, contract, fallback_value,
    fit_scaler, transform, FallbackAccumulator,
)

SCENARIOS = {"with_video_arrivals": "inputs", "operational": "operational_inputs", "without_feedback": "cold_inputs"}


def load_preparation(directory):
    quality = json.loads((directory / "quality.json").read_text())
    if quality.get("schema_version") != SCHEMA_VERSION or quality.get("contract") != contract(quality["window"]):
        raise ValueError("Preparación incompatible; regenera las secuencias v2")
    if not quality["can_train"]:
        raise ValueError("Sigue recopilando: cada partición necesita esperas observadas")
    if quality.get("sequences_sha256") != hashlib.sha256((directory / "sequences.jsonl").read_bytes()).hexdigest():
        raise ValueError("Las secuencias no corresponden al informe de preparación")
    samples = [json.loads(line) for line in (directory / "sequences.jsonl").read_text().splitlines()]
    for sample in samples:
        if sample["loss_weight"] and (sample["record_type"] != "user_wait" or not 0 <= sample["target"] <= MAX_WAIT):
            raise ValueError("Objetivo supervisado inválido")
    partitions = {name: [sample for sample in samples if sample["split"] == name]
                  for name in ("train", "validation", "test")}
    if any(not any(row["loss_weight"] for row in rows) for rows in partitions.values()):
        raise ValueError("Cada partición necesita esperas observadas")
    frozen = FallbackAccumulator()
    for row in sorted((r for r in partitions["train"] if r["loss_weight"]), key=lambda r: (r.get("available_at", r.get("bus_arrival")), r["sample_id"])):
        frozen.add(row["station_id"], row["event_at"], row["target"])
    if frozen.table != quality["fallback"]:
        raise ValueError("Los respaldos no corresponden exclusivamente a entrenamiento")
    return quality, partitions


def build_lstm(window):
    import tensorflow as tf
    values = tf.keras.Input(shape=(window, INPUT_WIDTH), name="sequence")
    mask = tf.keras.Input(shape=(window,), dtype="bool", name="step_mask")
    hidden = tf.keras.layers.LSTM(32, use_cudnn=False)(values, mask=mask)
    hidden = tf.keras.layers.Dense(16, activation="relu")(hidden)
    output = tf.keras.layers.Dense(1)(hidden)
    model = tf.keras.Model(inputs={"sequence": values, "step_mask": mask}, outputs=output)
    model.compile(optimizer="adam", loss="mse")
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Carpeta nueva")
    parser.add_argument("--version", required=True)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--synthetic", action="store_true", help="Prueba ML aislada; el candidato nunca es elegible")
    args = parser.parse_args()
    try:
        quality, partitions = load_preparation(args.data)
    except (ValueError, KeyError) as error:
        parser.error(str(error))
    if args.output.exists() or args.epochs < 1:
        parser.error("Usa carpeta nueva y epochs positivo")
    import numpy as np
    import tensorflow as tf
    from xgboost import XGBRegressor

    tf.keras.utils.set_random_seed(2026050)
    scaler = fit_scaler(partitions["train"])
    # Centinels remain in the exported preparation, but never become wait labels.
    supervised = {name: [sample for sample in rows if sample["loss_weight"]]
                  for name, rows in partitions.items()}

    def tensors(rows, branch):
        return {"sequence": np.array([transform(row[branch], scaler) for row in rows], dtype="float32"),
                "step_mask": np.array([[bool(v[0]) for v in row[branch]["step_mask"]] for row in rows], dtype="bool")}

    def baseline_inputs(values):
        return np.concatenate([values["sequence"].reshape(len(values["sequence"]), -1),
                               values["step_mask"].astype("float32")], axis=1)

    train_parts = [tensors(supervised["train"], branch) for branch in SCENARIOS.values()]
    train_inputs = {key: np.concatenate([part[key] for part in train_parts]) for key in train_parts[0]}
    targets = np.array([row["target"] for row in supervised["train"]], dtype="float32").reshape(-1, 1)
    train_targets = np.concatenate([targets] * len(train_parts))
    # A feedback and camera example retain their total weight across all three scenarios.
    sample_weights = np.tile(np.array([row["loss_weight"] / len(train_parts) for row in supervised["train"]], dtype="float32"), len(train_parts))
    validation = tensors(supervised["validation"], "operational_inputs")
    validation_targets = np.array([row["target"] for row in supervised["validation"]], dtype="float32").reshape(-1, 1)
    model = build_lstm(quality["window"])
    model.fit(train_inputs, train_targets, sample_weight=sample_weights, validation_data=(validation, validation_targets),
              epochs=args.epochs, batch_size=32, shuffle=False,
              callbacks=[tf.keras.callbacks.EarlyStopping(patience=6, restore_best_weights=True)], verbose=2)
    xgb = XGBRegressor(n_estimators=200, max_depth=4, learning_rate=.05,
                       objective="reg:squarederror", random_state=2026050)
    # Both learners see the entire window and its padding mask.
    xgb.fit(baseline_inputs(train_inputs), train_targets.reshape(-1), sample_weight=sample_weights)

    def predict(kind, rows, branch):
        if kind == "historical":
            values = []
            for row in rows:
                if row["split"] == "train":
                    # Prefix-only training fallback already embedded in cold_inputs.
                    past = [value[0] for value, origin in zip(row["cold_inputs"]["past_wait"], row["cold_inputs"]["wait_origin"]) if origin == [0, 1, 0]]
                    values.append(past[-1] if past else float("nan"))
                else:
                    values.append(fallback_value(quality["fallback"], row["station_id"], row["event_at"]))
            return np.array(values, dtype="float32")
        values = tensors(rows, branch)
        result = model.predict(values, verbose=0).reshape(-1) if kind == "lstm" else xgb.predict(baseline_inputs(values))
        return np.clip(result, 0, MAX_WAIT)

    def metrics(rows, predictions, radius):
        expected = np.array([row["target"] for row in rows])
        available = np.isfinite(predictions)
        y, p = expected[available], predictions[available]
        if len(y) == 0:
            return {"count": 0, "missing": len(rows), "mae": None, "rmse": None, "r2": None, "interval_coverage": None}
        residual = y - p
        variance = float(np.sum((y - y.mean()) ** 2))
        return {"count": len(y), "missing": len(rows) - len(y),
                "mae": float(np.mean(np.abs(residual))), "rmse": float(np.sqrt(np.mean(residual ** 2))),
                "r2": float(1 - np.sum(residual ** 2) / variance) if variance else None,
                "interval_coverage": float(np.mean(np.abs(residual) <= radius))}

    report, radii = {}, {}
    for kind in ("historical", "xgboost", "lstm"):
        residuals = np.abs(validation_targets.reshape(-1) - predict(kind, supervised["validation"], "operational_inputs"))
        radius = float(np.quantile(residuals, .9, method="higher"))
        radii[kind] = radius
        report[kind] = {
            part: {scenario: metrics(rows, predict(kind, rows, branch), radius)
                   for scenario, branch in SCENARIOS.items()}
            for part, rows in supervised.items()
        }
    # Validation is used for selection; test remains an independent report.
    operational_mae = report["lstm"]["validation"]["operational"]["mae"]
    cold_mae = report["lstm"]["validation"]["without_feedback"]["mae"]
    synthetic = bool(args.synthetic or quality.get("simulation"))
    eligible = bool(not synthetic and quality["meets_collection_goal"] and operational_mae < 5 and cold_mae < 5)
    args.output.mkdir(parents=True)
    model.save(args.output / "model.keras")
    xgb.save_model(args.output / "xgboost.json")
    metadata = {
        "schema_version": SCHEMA_VERSION, "model_version": args.version,
        "window": quality["window"], "contract": contract(quality["window"]),
        "scaler": scaler, "fallback": quality["fallback"], "radius": radii["lstm"],
        "model_sha256": hashlib.sha256((args.output / "model.keras").read_bytes()).hexdigest(),
        "dataset_sha256": hashlib.sha256((args.data / "sequences.jsonl").read_bytes()).hexdigest(),
        "quality_sha256": hashlib.sha256((args.data / "quality.json").read_bytes()).hexdigest(),
        "training_cutoff": max(row.get("available_at", row.get("bus_arrival")) for row in supervised["train"]),
        "simulation": synthetic,
        "calibration_cutoff": max(row.get("available_at", row.get("bus_arrival")) for row in supervised["validation"]),
        "training_sample_ids": [row["sample_id"] for row in supervised["train"]],
        "calibration_sample_ids": [row["sample_id"] for row in supervised["validation"]],
        "eligible": eligible, "approved": False, "metrics": report,
        "serving_bus_context": "always_missing; no live camera",
        "note": "Requires independent review including held-out test and feedback usefulness. No automatic deployment.",
    }
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Candidate saved: {args.output}; eligible={eligible}; approved=False")


if __name__ == "__main__":
    main()
