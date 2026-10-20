"""Versioned causal preprocessing, independent of Firebase and TensorFlow."""
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from math import isfinite, sqrt
from zoneinfo import ZoneInfo

SCHEMA_VERSION = "arribo-sequences-v2"
FEATURES = ["hour", "day_of_week", "is_weekend", "is_holiday", "precipitation_mm", "temp_c", "humidity"]
BUS_FEATURES = ["minutes_since_bus", "interval_1_min", "interval_2_min", "interval_3_min"]
UNITS = ["hour_0_23", "weekday_0_6", "binary", "binary", "mm", "degC", "percent"]
BRANCHES = (("temporal", "temporal_mask", 7), ("past_wait", "past_wait_mask", 1), ("bus_context", "bus_mask", 4))
INPUT_WIDTH = 27
WINDOWS = (10, 20, 30)
HORIZON_SECONDS = 7200
MAX_WAIT = 45
LOCAL_ZONE = ZoneInfo("America/Mexico_City")


def timestamp(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime):
        raise ValueError("Timestamp inválido")
    if result.tzinfo is None:
        result = result.replace(tzinfo=LOCAL_ZONE)
    return result.astimezone(timezone.utc)


def finite(value):
    if value is None or value == "":
        return None
    result = float(value)
    if not isfinite(result):
        raise ValueError("Valores no finitos")
    return result


@lru_cache(maxsize=32)
def _holidays(year):
    try:
        import holidays
    except ImportError:
        return None  # Unknown calendar is not a negative holiday observation.
    return holidays.Mexico(years=year)


def calendar_context(at):
    local = timestamp(at).astimezone(LOCAL_ZONE)
    dates = _holidays(local.year)
    return dict(hour=local.hour, day_of_week=local.weekday(),
                is_weekend=int(local.weekday() >= 5),
                is_holiday=int(local.date() in dates) if dates is not None else None)


def contract(window):
    if window not in WINDOWS:
        raise ValueError("Ventana permitida: 10, 20 o 30")
    return dict(schema_version=SCHEMA_VERSION, window=window, input_width=INPUT_WIDTH,
                features=FEATURES, units=UNITS, bus_features=BUS_FEATURES,
                bus_units=["minutes"] * 4, timezone="America/Mexico_City",
                horizon_seconds=HORIZON_SECONDS, max_wait=MAX_WAIT,
                wait_origin=["observed", "fallback", "missing"],
                branches=[list(branch) for branch in BRANCHES],
                fallback_policy="station-weekday-hour_then_station-hour_then_station-global",
                sequence_policy="available-reports_then_current; fallback-before-oldest-report-v1")


def select_observations(observations, now, station_id, window, start_at=None):
    """Check availability at prediction time, not at the report's own query."""
    contract(window)
    now = timestamp(now)
    lower = now - timedelta(seconds=HORIZON_SECONDS)
    if start_at is not None:
        lower = max(lower, timestamp(start_at))
    unique = {}
    for report in observations or []:
        if report.get("simulation") or report.get("outside_domain") or report.get("station_id", station_id) != station_id:
            continue
        try:
            query_at, received_at = timestamp(report["query_at"]), timestamp(report["received_at"])
            actual = finite(report["actual_min"])
        except (KeyError, TypeError, ValueError):
            continue
        if actual is None or not 0 <= actual <= MAX_WAIT or not lower <= query_at < now or not query_at <= received_at < now:
            continue
        identity = (str(report.get("uid", "")), str(report.get("query_id", "")))
        if not identity[1]:
            continue
        item = {**report, "query_at": query_at, "received_at": received_at, "actual_min": actual}
        if identity not in unique or received_at < unique[identity]["received_at"]:
            unique[identity] = item
    return sorted(unique.values(), key=lambda item: (item["query_at"], str(item.get("uid", "")), item["query_id"]))[-(window - 1):]


class FallbackAccumulator:
    """Accept only training observations at the caller's causal cutoff."""
    def __init__(self):
        self.table = {}

    def add(self, station_id, query_at, actual_min):
        value = finite(actual_min)
        if value is None or not 0 <= value <= MAX_WAIT:
            return
        context = calendar_context(query_at)
        station = self.table.setdefault(station_id, {})
        for key in (f"dh:{context['day_of_week']}:{context['hour']}", f"h:{context['hour']}", "global"):
            entry = station.setdefault(key, {"sum": 0.0, "count": 0})
            entry["sum"] += value
            entry["count"] += 1


def fallback_value(table, station_id, at):
    context = calendar_context(at)
    station = (table or {}).get(station_id, {})
    for key in (f"dh:{context['day_of_week']}:{context['hour']}", f"h:{context['hour']}", "global"):
        entry = station.get(key)
        if entry and entry["count"] > 0:
            value = finite(entry["sum"]) / entry["count"]
            if not 0 <= value <= MAX_WAIT:
                raise ValueError("Promedio fuera de dominio")
            return value
    return None


def bus_context_at(arrivals, at):
    """All arrivals must belong to the same verified continuity block."""
    buses = sorted(set(timestamp(value) for value in arrivals))
    known = buses[:bisect_left(buses, timestamp(at))]
    intervals = [(right - left).total_seconds() / 60 for left, right in zip(known, known[1:])]
    return [(timestamp(at) - known[-1]).total_seconds() / 60 if known else None,
            *list(reversed(intervals))[:3], *([None] * max(0, 3 - len(intervals)))]


def _step(context, at, wait=None, origin="missing", buses=None):
    calendar = calendar_context(at)
    values = {**calendar, **context}
    for name in FEATURES[:3]:
        values[name] = calendar[name]
    temporal = [finite(values.get(name)) for name in FEATURES]
    bounds = {0: (0, 23), 1: (0, 6), 2: (0, 1), 3: (0, 1), 4: (0, float("inf")), 6: (0, 100)}
    for index, value in enumerate(temporal):
        if value is not None and index in bounds and (not bounds[index][0] <= value <= bounds[index][1] or (index in (0, 1, 2, 3) and value != int(value))):
            raise ValueError(f"Contexto fuera de dominio: {FEATURES[index]}")
    bus_values = [finite(value) for value in (buses if buses is not None else [None] * 4)]
    if len(bus_values) != 4 or any(value is not None and value < 0 for value in bus_values):
        raise ValueError("Contexto de buses inválido")
    return {"temporal": [v if v is not None else 0 for v in temporal],
            "temporal_mask": [int(v is not None) for v in temporal],
            "past_wait": [wait if wait is not None else 0], "past_wait_mask": [int(wait is not None)],
            "bus_context": [v if v is not None else 0 for v in bus_values],
            "bus_mask": [int(v is not None) for v in bus_values],
            "wait_origin": [int(origin == name) for name in ("observed", "fallback", "missing")],
            "step_mask": [1]}


def online_inputs(context, window, *, now, station_id="rio_consulado", observations=None,
                  fallback=None, start_at=None, arrivals=None):
    """The same recipe for videos and serving; arrivals is for offline experiments.

    Never derive bus arrivals from feedback received_at or query_at + actual_min.
    """
    now = timestamp(now)
    reports = select_observations(observations, now, station_id, window, start_at)
    lower = now - timedelta(seconds=HORIZON_SECONDS)
    if start_at is not None:
        lower = max(lower, timestamp(start_at))
    missing = window - 1 - len(reports)
    edge = reports[0]["query_at"] if reports else now
    steps, provenance = [], []
    for i in range(missing):
        at = lower + (edge - lower) * ((i + 1) / (missing + 1))
        wait = fallback_value(fallback, station_id, at) if lower <= at < edge else None
        if wait is not None:
            steps.append(_step({}, at, wait, "fallback"))
            provenance.append({"kind": "fallback", "query_at": at.isoformat()})
        else:
            zero = _step({}, now)
            steps.append({key: [0] * len(value) for key, value in zero.items()})
            provenance.append({"kind": "padding"})
    for report in reports:
        at = report["query_at"]
        steps.append(_step(report["context"], at, report["actual_min"], "observed",
                           bus_context_at(arrivals, at) if arrivals is not None else None))
        provenance.append({"kind": "observed", "query_id": report["query_id"],
                           "query_at": at.isoformat(), "received_at": report["received_at"].isoformat()})
    steps.append(_step(context, now, buses=bus_context_at(arrivals, now) if arrivals is not None else None))
    provenance.append({"kind": "current", "query_at": now.isoformat()})
    inputs = {key: [step[key] for step in steps] for key in steps[0]}
    inputs["provenance"] = provenance
    validate_inputs(inputs, window)
    return inputs


def validate_inputs(inputs, window):
    contract(window)
    shapes = [(n, w) for n, m, w in BRANCHES] + [(m, w) for n, m, w in BRANCHES] + [("wait_origin", 3), ("step_mask", 1)]
    for name, width in shapes:
        values = inputs[name]
        if len(values) != window or any(len(row) != width for row in values):
            raise ValueError(f"Forma inválida: {name}")
        if any(finite(value) is None for row in values for value in row):
            raise ValueError("Los tensores no admiten null")
        if name.endswith("mask") or name == "wait_origin":
            if any(value not in (0, 1) for row in values for value in row):
                raise ValueError("Máscara inválida")
    if inputs["past_wait_mask"][-1] != [0] or inputs["past_wait"][-1] != [0] or inputs["wait_origin"][-1] != [0, 0, 1] or inputs["step_mask"][-1] != [1]:
        raise ValueError("El paso actual no puede contener el objetivo")
    for i in range(window):
        if inputs["step_mask"][i] == [1] and sum(inputs["wait_origin"][i]) != 1:
            raise ValueError("Origen de espera inválido")
        if inputs["step_mask"][i] == [0]:
            if any(value for name, _ in shapes for value in inputs[name][i]):
                raise ValueError("Relleno estructural no vacío")
        elif inputs["past_wait_mask"][i][0] != int(inputs["wait_origin"][i] != [0, 0, 1]):
            raise ValueError("Disponibilidad y origen inconsistentes")
        for name, mask_name, _ in BRANCHES:
            if any(value != 0 for value, mask in zip(inputs[name][i], inputs[mask_name][i]) if not mask):
                raise ValueError("Ausencia con valor distinto del relleno")


def fit_scaler(samples):
    """Count each measurement once, on explicitly marked training samples only."""
    columns = {name: [[] for _ in range(size)] for name, _, size in BRANCHES}
    seen = {name: set() for name in columns}
    for sample in samples:
        if sample.get("split") != "train":
            raise ValueError("El escalado solo acepta la partición train")
        inputs = sample["inputs"]
        validate_inputs(inputs, len(inputs["step_mask"]))
        identity = sample["sample_id"]
        for name, mask_name, _ in (BRANCHES[0], BRANCHES[2]):
            if identity in seen[name]:
                continue
            seen[name].add(identity)
            for i, value in enumerate(inputs[name][-1]):
                if inputs[mask_name][-1][i]:
                    columns[name][i].append(value)
        if sample.get("loss_weight") and identity not in seen["past_wait"]:
            seen["past_wait"].add(identity)
            columns["past_wait"][0].append(sample["target"])
    scaler = {}
    for name, values_by_column in columns.items():
        means, scales = [], []
        for values in values_by_column:
            mean = sum(values) / len(values) if values else 0
            variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0
            means.append(mean)
            scales.append(sqrt(variance) or 1)
        scaler[name] = {"mean": means, "scale": scales}
    return scaler


def transform(inputs, scaler):
    validate_inputs(inputs, len(inputs["step_mask"]))
    result = []
    for step, available in enumerate(inputs["step_mask"]):
        vector = []
        for name, mask_name, size in BRANCHES:
            stats = scaler[name]
            if len(stats["mean"]) != size or len(stats["scale"]) != size or any(not isfinite(v) for v in stats["mean"] + stats["scale"]) or any(v <= 0 for v in stats["scale"]):
                raise ValueError("Escalador inválido")
            masks = inputs[mask_name][step]
            vector.extend((value - stats["mean"][i]) / stats["scale"][i] if masks[i] and available[0] else 0 for i, value in enumerate(inputs[name][step]))
            vector.extend(masks if available[0] else [0] * size)
        vector.extend(inputs["wait_origin"][step] if available[0] else [0] * 3)
        result.append(vector)
    return result
