"""Audit the complete dataset and build causal, masked sequences (no ML deps).

python preparar_modelo.py --csv output/dataset_final.csv --output output/modelo
Without a verified manifest, continuity ends at each video/day.
"""
import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

TEMPORAL_FEATURES = ["hour", "day_of_week", "is_weekend", "is_holiday", "precipitation_mm", "temp_c", "humidity"]
BUS_FEATURES = ["minutes_since_bus", "interval_1_min", "interval_2_min", "interval_3_min"]


def number(value):
    if value in (None, ""):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Valores no finitos en el dataset")
    return result


def timestamp(value):
    return datetime.fromisoformat(value)


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for index, row in enumerate(rows, 2):
        required = {"track_id_persona", "video_source", "record_type", "wait_observed", "station_id", "camera_id", "arrival_user", "bus_arrival"}
        if not required.issubset(row):
            raise ValueError("Reexporta el CSV completo con las 18 columnas")
        track = int(row["track_id_persona"])
        sentinel = track == -1
        if track < -1 or row["record_type"] != ("bus_only" if sentinel else "user_wait") or int(row["wait_observed"]) != int(not sentinel):
            raise ValueError(f"Tipo/máscara inconsistente en fila {index}")
        row["target"] = number(row["waiting_time_min"])
        start, end = timestamp(row["arrival_user"]), timestamp(row["bus_arrival"])
        if row["target"] is None or row["target"] < 0 or end < start:
            raise ValueError(f"Espera inválida en fila {index}")
        if sentinel and row["target"] != 0:
            raise ValueError(f"Centinela con espera distinta de cero en fila {index}")
        if not sentinel and abs((end - start).total_seconds() / 60 - row["target"]) > .1:
            raise ValueError(f"Espera/timestamps inconsistentes en fila {index}")
        row["event_at"] = end if sentinel else start
    if not rows:
        raise ValueError("Dataset vacío")
    return rows


def read_manifest(path):
    if not path:
        return {}
    with Path(path).open(newline="", encoding="utf-8") as stream:
        manifest = list(csv.DictReader(stream))
    mapping = {}
    groups = defaultdict(list)
    for item in manifest:
        if item["verified"] != "1":
            continue
        key = (item["station_id"], item["camera_id"], item["video_source"], item["date"])
        if key in mapping or not item["continuity_group"]:
            raise ValueError("Manifiesto ambiguo o sin grupo")
        start, end = timestamp(item["start_at"]), timestamp(item["end_at"])
        if end <= start or start.date().isoformat() != item["date"]:
            raise ValueError("Intervalo inválido en manifiesto")
        group = (item["station_id"], item["camera_id"], item["date"], item["continuity_group"])
        mapping[key] = (group, start, end)
        groups[group].append((start, end))
    for intervals in groups.values():
        intervals.sort()
        for previous, following in zip(intervals, intervals[1:]):
            if abs((following[0] - previous[1]).total_seconds()) > 2:
                raise ValueError("Hay un hueco/solapamiento en un grupo que se declaró continuo")
    return mapping


def masked(values):
    return [value if value is not None else 0.0 for value in values], [int(value is not None) for value in values]


def chronological_split(rows):
    # Keep passengers of the same bus in the same split, including midnight.
    dates = sorted({timestamp(row["bus_arrival"]).date().isoformat() for row in rows})
    if len(dates) < 3:
        return {date: "collecting" for date in dates}, False
    train_end = min(len(dates) - 2, max(1, int(len(dates) * .7)))
    validation_end = min(len(dates) - 1, max(train_end + 1, int(len(dates) * .85)))
    return {date: "train" if i < train_end else "validation" if i < validation_end else "test" for i, date in enumerate(dates)}, True


def build(rows, window=20, manifest=None):
    if window not in (10, 20, 30):
        raise ValueError("Ventana permitida: 10, 20 o 30")
    manifest = manifest or {}
    groups = defaultdict(list)
    split, ready = chronological_split(rows)
    unique_buses = set()
    for row in rows:
        date = row["event_at"].date().isoformat()
        key = (row["station_id"], row["camera_id"], row["video_source"], date)
        verified = manifest.get(key)
        if verified and not verified[1] <= row["event_at"] <= verified[2]:
            raise ValueError("Evento fuera del intervalo verificado del video")
        group = verified[0] if verified else (*key, "unverified")
        groups[group].append(row)
        unique_buses.add((row["station_id"], row["camera_id"], row["bus_arrival"]))
    examples = []
    for group, events in groups.items():
        events.sort(key=lambda row: row["event_at"])
        buses = sorted({timestamp(row["bus_arrival"]) for row in events})
        observations = defaultdict(list)
        for row in events:
            if row["record_type"] == "user_wait":
                observations[timestamp(row["bus_arrival"])].append(row["target"])
        past_steps = []
        for row in events:
            now = row["event_at"]
            # Equal-time completed observations are excluded conservatively.
            known_buses = [bus for bus in buses if bus < now]
            known_waits = [(at, sum(values) / len(values)) for at, values in sorted(observations.items()) if at < now and (now - at).total_seconds() <= 7200]
            temporal, temporal_mask = masked([now.hour, now.weekday(), int(now.weekday() >= 5), *[number(row[key]) for key in TEMPORAL_FEATURES[3:]]])
            intervals = [(right - left).total_seconds() / 60 for left, right in zip(known_buses, known_buses[1:])]
            latest = list(reversed(intervals))[:3]
            bus, bus_mask = masked([(now - known_buses[-1]).total_seconds() / 60 if known_buses else None, *latest, *([None] * (3 - len(latest)))])
            wait, wait_mask = masked([known_waits[-1][1] if known_waits else None])
            step = {"temporal": temporal, "temporal_mask": temporal_mask, "past_wait": wait, "past_wait_mask": wait_mask, "bus_context": bus, "bus_mask": bus_mask, "step_mask": [1]}
            past_steps.append(step)
            zero = {key: [0] * len(value) for key, value in step.items()}
            sequence = [zero] * max(0, window - len(past_steps)) + past_steps[-window:]
            examples.append({"event_at": now.isoformat(), "bus_arrival": row["bus_arrival"], "video_source": row["video_source"], "station_id": row["station_id"], "camera_id": row["camera_id"], "record_type": row["record_type"], "split": split[timestamp(row["bus_arrival"]).date().isoformat()], "target": row["target"], "loss_weight": int(row["wait_observed"]), "inputs": {key: [item[key] for item in sequence] for key in step}})
    examples.sort(key=lambda item: item["event_at"])
    report = {"rows": len(rows), "user_wait": sum(row["record_type"] == "user_wait" for row in rows), "bus_only": sum(row["record_type"] == "bus_only" for row in rows), "unique_buses": len(unique_buses), "dates": sorted(split), "coverage_by_hour": dict(sorted(Counter(str(row["event_at"].hour) for row in rows).items())), "split_counts": dict(Counter(item["split"] for item in examples)), "can_train": ready, "warning": "Una sola fecha no permite validar generalización" if not ready else "La separación temporal es necesaria, pero no garantiza diversidad/calidad suficiente", "continuity": "Manifiesto verificado; videos no declarados permanecen aislados" if manifest else "Conservadora: cada video/fecha aislado", "temporal_features": TEMPORAL_FEATURES, "bus_features": BUS_FEATURES, "window": window, "schema_version": "arribo-sequences-v1"}
    return examples, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--window", type=int, choices=[10, 20, 30], default=20)
    args = parser.parse_args()
    examples, report = build(read_rows(args.csv), args.window, read_manifest(args.manifest))
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "sequences.jsonl").open("w", encoding="utf-8") as stream:
        for item in examples:
            stream.write(json.dumps(item) + "\n")
    with (args.output / "quality.json").open("w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
