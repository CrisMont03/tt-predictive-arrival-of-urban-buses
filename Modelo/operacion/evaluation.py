"""Comparable operational benchmark, never the thesis final test."""
import math

from app.artifacts import digest, json_bytes
from app.contracts import Context
from app.model_inputs import SCHEMA_VERSION, timestamp


def compare(benchmark, candidate, incumbent, *, synthetic=False):
    if (benchmark.get("format") != "arribo-benchmark-v1" or benchmark.get("purpose") != "monthly"
            or benchmark.get("simulation") is not synthetic):
        raise ValueError("Benchmark incompatible, final de tesis o de otro entorno")
    cases = benchmark.get("cases", [])
    if not cases or len({row["sample_id"] for row in cases}) != len(cases):
        raise ValueError("Benchmark vacío o duplicado")
    ids = {row["sample_id"] for row in cases}
    result = dict(benchmark_sha256=digest(json_bytes(benchmark)), count=len(cases), simulation=synthetic,
                  benchmark=benchmark,
                  candidate_version=candidate.metadata["model_version"],
                  incumbent_version=incumbent.metadata["model_version"] if incumbent else None)
    for label, predictor in (("candidate", candidate), ("incumbent", incumbent)):
        if predictor is None:
            continue
        meta = predictor.metadata
        if (meta.get("schema_version") != SCHEMA_VERSION or bool(meta.get("simulation")) != synthetic
                or not {"training_sample_ids", "calibration_sample_ids"} <= meta.keys()):
            raise ValueError("El artefacto carece de contrato o trazabilidad comparable")
        if ids.intersection(meta["training_sample_ids"] + meta["calibration_sample_ids"]):
            raise ValueError("El benchmark fue utilizado para ajustar el modelo")
        errors = {"operational": [], "without_feedback": []}
        for row in cases:
            if row.get("simulation") is not synthetic or row["station_id"] != "rio_consulado":
                raise ValueError("Caso simulado o estación incompatible")
            target = row["target"]
            if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(target) or not 0 <= target <= 45:
                raise ValueError("Objetivo inválido")
            now = timestamp(row["query_at"])
            if now <= timestamp(meta["training_cutoff"]):
                raise ValueError("El benchmark no es posterior al ajuste del modelo")
            if meta.get("calibration_cutoff") and now <= timestamp(meta["calibration_cutoff"]):
                raise ValueError("El benchmark no es posterior a la calibración")
            context = Context.model_validate(row["context"]).model_dump()
            for scenario, observations in (("operational", row.get("observations", [])), ("without_feedback", [])):
                value, _ = predictor.predict(context, observations, now=now, station_id=row["station_id"])
                if not math.isfinite(value) or not 0 <= value <= 45:
                    raise ValueError("Predicción inválida o cobertura incompleta")
                errors[scenario].append(abs(value - target))
        result[label] = {key: sum(values) / len(values) for key, values in errors.items()}
    compatible = (candidate.metadata.get("eligible") is True and not synthetic)
    below_goal = all(value < 5 for value in result["candidate"].values())
    improves = bool(incumbent and result["candidate"]["operational"] < result["incumbent"]["operational"])
    result["decision"] = ("fixture_improves" if improves else "fixture_keeps") if synthetic else (
        "manual_initial_approval" if incumbent is None and compatible and below_goal else
        "candidate_improves" if improves and compatible and below_goal else "keep_incumbent")
    result["publishable"] = bool(not synthetic and incumbent and improves and compatible and below_goal)
    return result
