"""Train local candidates. Search evaluates validation only; never approve/deploy."""
import argparse
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "Backend")]
from app.model_inputs import (INPUT_WIDTH, MAX_WAIT, SCHEMA_VERSION, FEEDBACK_SCHEMA, SUPPORTED_SCHEMAS, contract,
                             fallback_value, fit_scaler, transform, FallbackAccumulator,
                             calendar_context)
from app.model_inputs import is_synthetic_source

SCENARIOS = {"with_video_arrivals": "inputs", "operational": "operational_inputs",
             "without_feedback": "cold_inputs"}


def selected_training_scenarios(mode="all"):
    """Evaluation always keeps three scenarios; only fitting is configurable."""
    if mode not in ("all", "operational"):
        raise ValueError("Escenarios de entrenamiento inválidos")
    return {key: branch for key, branch in SCENARIOS.items()
            if mode == "all" or key != "with_video_arrivals"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def load_preparation(directory, *, include_test=False):
    directory = Path(directory)
    quality = json.loads((directory / "quality.json").read_text())
    if quality.get("schema_version") not in SUPPORTED_SCHEMAS or quality.get("contract") != contract(quality["window"], quality["schema_version"]):
        raise ValueError("Preparación incompatible; regenera las secuencias v2")
    if not quality["can_train"]:
        raise ValueError("Cada partición necesita esperas observadas")
    if quality.get("sequences_sha256") != sha256(directory / "sequences.jsonl"):
        raise ValueError("Las secuencias no corresponden al informe de preparación")
    names = ("train", "validation", "test") if include_test else ("train", "validation")
    partitions = {name: [] for name in names}
    with (directory / "sequences.jsonl").open() as stream:
        for line in stream:
            sample = json.loads(line)
            if sample["split"] not in partitions:
                continue
            if sample["loss_weight"] and (sample["record_type"] != "user_wait" or not 0 <= sample["target"] <= MAX_WAIT):
                raise ValueError("Objetivo supervisado inválido")
            partitions[sample["split"]].append(sample)
    if any(not any(row["loss_weight"] for row in rows) for rows in partitions.values()):
        raise ValueError("Cada partición necesita esperas observadas")
    ids = [row["sample_id"] for rows in partitions.values() for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("IDs repetidos entre ejemplos o particiones")
    augmented = quality.get("training_variant") == "real_augmented"
    if augmented:
        from Dataset.preparar_aumento import validate_mixture
        quality["_augmentation_info"] = validate_mixture(directory,quality,partitions)
    elif any(is_synthetic_source(row) for rows in partitions.values() for row in rows):
        raise ValueError("Filas sintéticas sin manifiesto de aumento")
    frozen = FallbackAccumulator()
    for row in sorted((r for r in partitions["train"] if r["loss_weight"] and not is_synthetic_source(r)),
                      key=lambda r: (r.get("available_at", r.get("bus_arrival")), r["sample_id"])):
        frozen.add(row["station_id"], row["event_at"], row["target"])
    if frozen.table != quality["fallback"]:
        raise ValueError("Los respaldos no corresponden exclusivamente a entrenamiento")
    return quality, partitions


def training_weights(rows,scenarios=3):
    """Keep total weight equal to the real baseline; generated mass is half the real mass."""
    import numpy as np
    real=sum(row["loss_weight"] for row in rows if not is_synthetic_source(row))
    generated=sum(row["loss_weight"] for row in rows if is_synthetic_source(row))
    if generated and not real:
        raise ValueError("El aumento requiere observaciones reales")
    factor=.5*real/generated if generated else 1
    scale=real/(real+.5*real) if generated else 1
    values=np.array([row["loss_weight"]*(factor if is_synthetic_source(row) else 1)*scale/scenarios
                     for row in rows],dtype="float32")
    return np.tile(values,scenarios)


def build_lstm(window, units=32, learning_rate=.001):
    import tensorflow as tf
    values = tf.keras.Input(shape=(window, INPUT_WIDTH), name="sequence")
    mask = tf.keras.Input(shape=(window,), dtype="bool", name="step_mask")
    hidden = tf.keras.layers.LSTM(units, use_cudnn=False)(values, mask=mask)
    hidden = tf.keras.layers.Dense(16, activation="relu")(hidden)
    model = tf.keras.Model(inputs={"sequence": values, "step_mask": mask}, outputs=tf.keras.layers.Dense(1)(hidden))
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=learning_rate), loss="mse")
    return model


def tensors(rows, branch, scaler):
    import numpy as np
    return {"sequence": np.array([transform(row[branch], scaler) for row in rows], dtype="float32"),
            "step_mask": np.array([[bool(v[0]) for v in row[branch]["step_mask"]] for row in rows], dtype="bool")}


def baseline_inputs(values):
    import numpy as np
    return np.concatenate([values["sequence"].reshape(len(values["sequence"]), -1),
                           values["step_mask"].astype("float32")], axis=1)


def predictions(kind, model, rows, branch, metadata):
    import numpy as np
    if kind == "historical":
        return np.array([fallback_value(metadata["fallback"], row["station_id"], row["event_at"])
                         for row in rows], dtype="float64")
    values = tensors(rows, branch, metadata["scaler"])
    result = (model(values, training=False).numpy().reshape(-1) if kind == "lstm"
              else model.predict(baseline_inputs(values)))
    return np.clip(result, 0, MAX_WAIT)


def metrics(rows, values, radius):
    import numpy as np
    expected = np.array([row["target"] for row in rows], dtype="float64")
    available = np.isfinite(values)
    y, p = expected[available], values[available]
    if not len(y):
        return dict(count=0, missing=len(rows), mae=None, rmse=None, r2=None,
                    interval_coverage=None, interval_width=None)
    residual = y - p
    variance = float(np.sum((y - y.mean()) ** 2))
    lower, upper = np.maximum(0, p-radius), p+radius
    return dict(count=len(y), missing=len(rows)-len(y), mae=float(np.mean(np.abs(residual))),
                rmse=float(np.sqrt(np.mean(residual ** 2))),
                r2=float(1-np.sum(residual ** 2)/variance) if variance else None,
                interval_coverage=float(np.mean((y >= lower) & (y <= upper))),
                interval_width=float(np.mean(upper-lower)))


def evaluate(kind, model, rows, metadata):
    """Score explicitly supplied cases; never select a data partition here."""
    report, records = {}, []
    for scenario, branch in SCENARIOS.items():
        values = predictions(kind, model, rows, branch, metadata)
        radius = metadata["radii"][kind]
        report[scenario] = metrics(rows, values, radius)
        by_hour = {}
        for hour in sorted({calendar_context(row["event_at"])["hour"] for row in rows}):
            indices = [i for i, row in enumerate(rows) if calendar_context(row["event_at"])["hour"] == hour]
            by_hour[str(hour)] = metrics([rows[i] for i in indices], values[indices], radius)
        report[scenario]["by_hour"] = by_hour
        for row, value in zip(rows, values):
            p = float(value) if math.isfinite(float(value)) else None
            records.append(dict(sample_id=row["sample_id"], event_at=row["event_at"], scenario=scenario,
                                target=row["target"], predicted_min=p,
                                interval=None if p is None else [max(0, p-radius), p+radius]))
    return report, records


def train(data, output, version, *, model_kind="all", epochs=50, units=32, max_depth=4,
          seed=2026050, learning_rate=.001, synthetic=False, purpose="search",augmentation_manifest=None,
          training_scenarios="all"):
    started = time.monotonic()
    data, output = Path(data), Path(output)
    if output.exists() or epochs < 1 or units < 1 or max_depth < 1 or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("Usa carpeta nueva y parámetros positivos")
    if model_kind not in ("all", "historical", "xgboost", "lstm") or purpose not in ("search", "pilot", "maintenance"):
        raise ValueError("Tipo de experimento inválido")
    fitting_scenarios = selected_training_scenarios(training_scenarios)
    quality, partitions = load_preparation(data)
    augmented=quality.get("training_variant")=="real_augmented"
    if augmented:
        if (synthetic or augmentation_manifest is None
                or Path(augmentation_manifest).resolve()!=(data/"augmentation.json").resolve()):
            raise ValueError("El aumento requiere su manifiesto explícito y no utiliza --synthetic")
    elif augmentation_manifest is not None:
        raise ValueError("El manifiesto no corresponde a una preparación aumentada")
    import numpy as np
    scaler = quality["_augmentation_info"]["scaler"] if augmented else fit_scaler(partitions["train"])
    supervised = {name: [row for row in rows if row["loss_weight"]] for name, rows in partitions.items()}
    selected = ("historical", "xgboost", "lstm") if model_kind == "all" else (model_kind,)
    models, history = {}, None
    metadata = dict(schema_version=quality["schema_version"], model_version=version, window=quality["window"],
                    contract=contract(quality["window"], quality["schema_version"]), scaler=scaler, fallback=quality["fallback"])
    if set(selected) != {"historical"}:
        parts = [tensors(supervised["train"], branch, scaler) for branch in fitting_scenarios.values()]
        train_inputs = {key: np.concatenate([part[key] for part in parts]) for key in parts[0]}
        targets = np.array([row["target"] for row in supervised["train"]], dtype="float32").reshape(-1, 1)
        train_targets = np.concatenate([targets] * len(parts))
        weights = training_weights(supervised["train"],len(parts))
    if "lstm" in selected:
        os.environ.setdefault("TF_NUM_INTEROP_THREADS", "2")
        os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "2")
        import tensorflow as tf
        tf.keras.utils.set_random_seed(seed)
        tf.config.experimental.enable_op_determinism()
        model = build_lstm(quality["window"], units, learning_rate)
        options = tf.data.Options()
        options.threading.private_threadpool_size = 1
        options.threading.max_intra_op_parallelism = 2
        training = tf.data.Dataset.from_tensor_slices((train_inputs, train_targets, weights)).batch(32).with_options(options)
        val_inputs = tensors(supervised["validation"], "operational_inputs", scaler)
        val_targets = np.array([row["target"] for row in supervised["validation"]], dtype="float32").reshape(-1, 1)
        validation = tf.data.Dataset.from_tensor_slices((val_inputs, val_targets)).batch(32).with_options(options)
        fitted = model.fit(training, validation_data=validation, epochs=epochs, shuffle=False,
                           callbacks=[tf.keras.callbacks.EarlyStopping(patience=6, restore_best_weights=True)], verbose=2)
        history = {key: [float(v) for v in values] for key, values in fitted.history.items()}
        models["lstm"] = model
    if "xgboost" in selected:
        from xgboost import XGBRegressor
        model = XGBRegressor(n_estimators=200, max_depth=max_depth, learning_rate=.05,
                             objective="reg:squarederror", random_state=seed, n_jobs=2)
        model.fit(baseline_inputs(train_inputs), train_targets.reshape(-1), sample_weight=weights)
        models["xgboost"] = model
    radii, report, records = {}, {}, []
    validation = supervised["validation"]
    expected = np.array([row["target"] for row in validation])
    for kind in selected:
        values = predictions(kind, models.get(kind), validation, "operational_inputs", metadata)
        finite = np.abs(expected-values)
        finite = finite[np.isfinite(finite)]
        if not len(finite):
            raise ValueError("No hay predicciones válidas para calibrar el intervalo")
        radii[kind] = float(np.quantile(finite, .9, method="higher"))
        result, rows = evaluate(kind, models.get(kind), validation, {**metadata, "radii": radii})
        report[kind] = {"validation": result}
        records.extend({**row, "learner": kind} for row in rows)
    synthetic = bool(synthetic or quality.get("simulation"))
    scores = report.get("lstm", {}).get("validation", {})
    eligible = bool(purpose != "pilot" and not synthetic and not augmented and quality["meets_collection_goal"] and scores
                    and all(scores[name]["missing"] == 0 and scores[name]["mae"] < 5
                            for name in ("operational", "without_feedback")))
    output.mkdir(parents=True)
    for kind, model in models.items():
        model.save(output / "model.keras") if kind == "lstm" else model.save_model(output / "xgboost.json")
        if kind == "lstm":
            restored = tf.keras.models.load_model(output/"model.keras", compile=False, safe_mode=True)
        else:
            restored = XGBRegressor()
            restored.load_model(output/"xgboost.json")
        probe = validation[:2]
        before = predictions(kind, model, probe, "operational_inputs", metadata)
        after = predictions(kind, restored, probe, "operational_inputs", metadata)
        if not np.allclose(before, after, atol=1e-5, rtol=1e-5):
            raise ValueError("La predicción cambió al guardar/cargar el candidato")
    primary = "lstm" if "lstm" in selected else selected[0]
    sources = [Path(__file__), ROOT/"Dataset/preparar_modelo.py", ROOT/"Dataset/consolidar_dataset.py", ROOT/"Backend/app/model_inputs.py"]
    if augmented:
        sources += [ROOT/"Dataset/generar_sinteticos.py",ROOT/"Dataset/preparar_aumento.py"]
    metadata.update(
        learner=primary, learners=list(selected), radius=radii[primary], radii=radii,
        artifact_hashes={name: sha256(output/name) for name in ("model.keras", "xgboost.json") if (output/name).exists()},
        dataset_sha256=sha256(data/"sequences.jsonl"), quality_sha256=sha256(data/"quality.json"),
        source_sha256=quality.get("source_sha256"), partitions=quality.get("partitions"),
        training_variant=quality.get("training_variant", "unspecified"),
        contains_synthetic_training=augmented,
        augmentation_manifest_sha256=quality.get("augmentation_manifest_sha256"),
        preprocessing_reference=quality.get("_augmentation_info",{}).get("manifest"),
        training_cutoff=max(row.get("available_at", row.get("bus_arrival")) for row in supervised["train"]),
        calibration_cutoff=max(row.get("available_at", row.get("bus_arrival")) for row in validation),
        training_sample_ids=[row["sample_id"] for row in supervised["train"]],
        calibration_sample_ids=[row["sample_id"] for row in validation], simulation=synthetic,
        eligible=eligible, approved=False, metrics=report, evaluation_partitions=["validation"],
        serving_bus_context=("feedback-derived-arrivals-v1" if quality["schema_version"] == FEEDBACK_SCHEMA else "always_missing; no live camera"), purpose=purpose,
        training_config=dict(model=model_kind, epochs=epochs, units=units, max_depth=max_depth,
                             seed=seed, learning_rate=learning_rate, batch_size=32, patience=6,
                             training_scenarios=training_scenarios),
        training_scenarios=list(fitting_scenarios),
        effective_parameters={kind:(dict(n_estimators=200,max_depth=max_depth,learning_rate=.05)
                              if kind=="xgboost" else dict(units=units,dense_units=16,learning_rate=learning_rate,
                              batch_size=32,max_epochs=epochs,patience=6) if kind=="lstm" else dict(policy="training_fallback")) for kind in selected},
        weight_policy=(dict(real_base=1,synthetic_total_fraction=.5,normalize_total_to_real=True,
                           scenario_divisor=len(fitting_scenarios)) if augmented else dict(scenario_divisor=len(fitting_scenarios))),
        code_hashes={str(p.relative_to(ROOT)): sha256(p) for p in sources},
        dependencies={name: importlib.metadata.version(name) for name in ("numpy", "holidays", *(["tensorflow", "keras"] if "lstm" in selected else []), *(["xgboost", "scikit-learn"] if "xgboost" in selected else []))},
        duration_seconds=round(time.monotonic()-started, 3),
        peak_rss_bytes=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*(1 if sys.platform == "darwin" else 1024),
        roundtrip_verified=True, python_version=sys.version,
        counts={name: len(rows) for name, rows in supervised.items()},
        real_train_waits=sum(not is_synthetic_source(row) for row in supervised["train"]),
        synthetic_train_waits=sum(is_synthetic_source(row) for row in supervised["train"]),
        collection_goal_met=quality["meets_collection_goal"],
        note="Validation only. Test/reserve remain closed; independent manual review required. No deployment.")
    if "lstm" in models:
        metadata["model_sha256"] = metadata["artifact_hashes"]["model.keras"]
    write_json(output/"metadata.json", metadata)
    if history is not None:
        write_json(output/"history.json", history)
    with (output/"validation_predictions.jsonl").open("x") as stream:
        for row in records:
            stream.write(json.dumps(row, allow_nan=False)+"\n")
    print(f"Candidate saved: {output}; eligible={eligible}; approved=False; validation only")
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--model", choices=["all", "historical", "xgboost", "lstm"], default="all")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--units", type=int, default=32)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--seed", type=int, default=2026050)
    parser.add_argument("--learning-rate", type=float, default=.001)
    parser.add_argument("--purpose", choices=["search", "pilot", "maintenance"], default="search")
    parser.add_argument("--synthetic", action="store_true", help="Prueba ML aislada, nunca elegible")
    parser.add_argument("--augmentation-manifest",type=Path,help="Aumento offline controlado; no simulaciones de infraestructura")
    parser.add_argument("--training-scenarios", choices=["all", "operational"], default="all",
                        help="Escenarios para ajustar pesos; la evaluación conserva los tres escenarios")
    args = parser.parse_args()
    try:
        train(args.data, args.output, args.version, model_kind=args.model, epochs=args.epochs,
              units=args.units, max_depth=args.max_depth, seed=args.seed, learning_rate=args.learning_rate,
              synthetic=args.synthetic, purpose=args.purpose,augmentation_manifest=args.augmentation_manifest,
              training_scenarios=args.training_scenarios)
    except (ValueError, KeyError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
