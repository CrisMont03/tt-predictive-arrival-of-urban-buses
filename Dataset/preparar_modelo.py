"""Build causal sequences without training a model or changing source data."""
import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Backend"))
from app.model_inputs import (
    BUS_FEATURES, FEATURES, LOCAL_ZONE, MAX_WAIT, SCHEMA_VERSION,
    FallbackAccumulator, calendar_context, contract, finite, online_inputs, timestamp,
)

TEMPORAL_FEATURES = FEATURES
number = finite


def local_date(value):
    return timestamp(value).astimezone(LOCAL_ZONE).date().isoformat()


def read_rows(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    seen = set()
    required = {"track_id_persona", "video_source", "record_type", "wait_observed",
                "station_id", "camera_id", "arrival_user", "bus_arrival",
                "waiting_time_min", "is_holiday", "precipitation_mm", "temp_c", "humidity"}
    for index, row in enumerate(rows, 2):
        if not required.issubset(row):
            raise ValueError("Reexporta el CSV completo con las 18 columnas")
        track = int(row["track_id_persona"])
        sentinel = track == -1
        if track < -1 or row["record_type"] != ("bus_only" if sentinel else "user_wait") or int(row["wait_observed"]) != int(not sentinel):
            raise ValueError(f"Tipo/máscara inconsistente en fila {index}")
        if not all(row[key].strip() for key in ("station_id", "camera_id", "video_source")):
            raise ValueError(f"Procedencia vacía en fila {index}")
        row["target"] = number(row["waiting_time_min"])
        start, end = timestamp(row["arrival_user"]), timestamp(row["bus_arrival"])
        if row["target"] is None or row["target"] < 0 or end < start:
            raise ValueError(f"Espera inválida en fila {index}")
        if sentinel and (row["target"] != 0 or start != end):
            raise ValueError(f"Centinela inválido en fila {index}")
        if not sentinel and abs((end - start).total_seconds() / 60 - row["target"]) > .1:
            raise ValueError(f"Espera/timestamps inconsistentes en fila {index}")
        row["event_at"] = end if sentinel else start
        identity = [row[key] for key in ("station_id", "camera_id", "video_source", "track_id_persona")]
        identity += [start.isoformat(), end.isoformat()]
        row["sample_id"] = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        if row["sample_id"] in seen:
            raise ValueError(f"Evento duplicado en fila {index}")
        seen.add(row["sample_id"])
    if not rows:
        raise ValueError("Dataset vacío")
    return rows


def read_manifest(path):
    if not path:
        return {}
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        manifest = list(csv.DictReader(stream))
    mapping, groups = {}, defaultdict(list)
    for item in manifest:
        if item["verified"] != "1":
            continue
        key = (item["station_id"], item["camera_id"], item["video_source"], item["date"])
        if key in mapping or not item["continuity_group"]:
            raise ValueError("Manifiesto ambiguo o sin grupo")
        start, end = timestamp(item["start_at"]), timestamp(item["end_at"])
        if end <= start or local_date(start) != item["date"]:
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


def chronological_split(rows):
    # Entire bus groups remain together; dates refer to local bus completion.
    dates = sorted({local_date(row["bus_arrival"]) for row in rows})
    if len(dates) < 3:
        return {date: "collecting" for date in dates}, False
    train_end = min(len(dates) - 2, max(1, int(len(dates) * .7)))
    validation_end = min(len(dates) - 1, max(train_end + 1, int(len(dates) * .85)))
    return {date: "train" if i < train_end else "validation" if i < validation_end else "test"
            for i, date in enumerate(dates)}, True


def row_context(row):
    return {**calendar_context(row["event_at"]),
            **{key: number(row[key]) for key in FEATURES[3:]}}


def build(rows, window=10, manifest=None):
    specification = contract(window)
    manifest = manifest or {}
    split, ready = chronological_split(rows)
    groups, group_starts = defaultdict(list), {}
    verified_bounds = {}
    for group, start, end in manifest.values():
        previous = verified_bounds.get(group, (start, end))
        verified_bounds[group] = (min(start, previous[0]), max(end, previous[1]))
    partition_ends = {}
    for row in rows:
        part = split[local_date(row["bus_arrival"])]
        partition_ends[part] = max(partition_ends.get(part, timestamp(row["bus_arrival"])), timestamp(row["bus_arrival"]))
    cutoffs = {"validation": partition_ends.get("train"),
               "test": partition_ends.get("validation", partition_ends.get("train"))}
    normalized = []
    for original in rows:
        row = dict(original)
        date = local_date(row["event_at"])
        key = (row["station_id"], row["camera_id"], row["video_source"], date)
        verified = manifest.get(key)
        part = split[local_date(row["bus_arrival"])]
        if verified:
            if not verified[1] <= row["event_at"] <= verified[2] or timestamp(row["bus_arrival"]) > verified_bounds[verified[0]][1]:
                raise ValueError("Consulta o finalización fuera de la cobertura verificada")
            base_group = verified[0]
            start = verified_bounds[base_group][0]
        else:
            # No verified cross-day or cross-video completion may be assumed.
            if local_date(row["bus_arrival"]) != date:
                raise ValueError("Espera cruza jornada sin cobertura verificada")
            base_group = (*key, "unverified")
            start = row["event_at"]
        group = (*base_group, part)
        row["split"], row["group"] = part, group
        row["context"] = row_context(row)
        cutoff = cutoffs.get(part)
        row["boundary_excluded"] = bool(cutoff and row["event_at"] <= cutoff)
        row["eligible"] = row["record_type"] == "user_wait" and row["target"] <= MAX_WAIT and not row["boundary_excluded"]
        groups[group].append(row)
        group_starts[group] = min(start, group_starts.get(group, start))
        if cutoff is not None:
            group_starts[group] = max(group_starts[group], cutoff)
        normalized.append(row)

    def observation(row):
        return dict(query_id=row["sample_id"], station_id=row["station_id"],
                    query_at=row["event_at"], received_at=timestamp(row["bus_arrival"]),
                    actual_min=row["target"], context=row["context"],
                    simulation=False, outside_domain=False)

    train = sorted((row for row in normalized if row["split"] == "train" and row["eligible"]),
                   key=lambda row: (timestamp(row["bus_arrival"]), row["sample_id"]))
    frozen = FallbackAccumulator()
    for row in train:
        frozen.add(row["station_id"], row["event_at"], row["target"])
    reports = {group: [observation(row) for row in events if row["eligible"]]
               for group, events in groups.items()}
    buses = {group: sorted({timestamp(row["bus_arrival"]) for row in events if not row["boundary_excluded"]})
             for group, events in groups.items()}
    prefix, position = FallbackAccumulator(), 0
    examples = []
    for row in sorted(normalized, key=lambda row: (row["event_at"], row["sample_id"])):
        now, group = row["event_at"], row["group"]
        while position < len(train) and timestamp(train[position]["bus_arrival"]) < now:
            source = train[position]
            prefix.add(source["station_id"], source["event_at"], source["target"])
            position += 1
        # Training never sees its own label through a target-derived average.
        table = prefix.table if row["split"] in ("train", "collecting") else frozen.table
        if row["boundary_excluded"]:
            table = {}  # Quarantined boundary examples must not see future averages.
        kwargs = dict(now=now, station_id=row["station_id"], observations=reports[group],
                      fallback=table, start_at=group_starts[group])
        inputs = online_inputs(row["context"], window, arrivals=buses[group], **kwargs)
        operational = online_inputs(row["context"], window, **kwargs)
        cold = online_inputs(row["context"], window, **{**kwargs, "observations": []})
        examples.append(dict(sample_id=row["sample_id"], event_at=now.isoformat(),
                             bus_arrival=timestamp(row["bus_arrival"]).isoformat(),
                             video_source=row["video_source"], station_id=row["station_id"],
                             camera_id=row["camera_id"], record_type=row["record_type"],
                             split=row["split"], continuity_group=list(group),
                             boundary_excluded=row["boundary_excluded"], outside_domain=row["target"] > MAX_WAIT,
                             target=row["target"], loss_weight=int(row["eligible"]),
                             inputs=inputs, operational_inputs=operational, cold_inputs=cold))
    report = dict(rows=len(rows), user_wait=sum(r["record_type"] == "user_wait" for r in normalized),
                  bus_only=sum(r["record_type"] == "bus_only" for r in normalized),
                  unique_buses=sum(len(values) for values in buses.values()),
                  dates=sorted(split), split_counts=dict(Counter(r["split"] for r in examples)),
                  coverage_by_hour=dict(sorted(Counter(str(calendar_context(r["event_at"])["hour"]) for r in normalized).items())),
                  eligible_waits=sum(r["eligible"] for r in normalized),
                  boundary_excluded=sum(r["boundary_excluded"] for r in normalized),
                  can_train=ready and all(any(r["split"] == part and r["eligible"] for r in normalized) for part in ("train", "validation", "test")),
                  meets_collection_goal=sum(r["eligible"] for r in normalized) >= 5000,
                  warning="Preparación estructural, no aprobación de calidad ni precisión.",
                  continuity="Cada video/jornada/partición aislado salvo manifiesto verificado.",
                  temporal_features=FEATURES, bus_features=BUS_FEATURES, window=window,
                  schema_version=SCHEMA_VERSION, contract=specification, fallback=frozen.table,
                  serving_bus_context="always_missing; no live camera")
    return examples, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Carpeta nueva; no se sobrescribe")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--window", type=int, choices=[10, 20, 30], default=10)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Usa una carpeta nueva para conservar resultados anteriores")
    rows = read_rows(args.csv)
    examples, report = build(rows, args.window, read_manifest(args.manifest))
    report["source_sha256"] = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    report["manifest_sha256"] = hashlib.sha256(args.manifest.read_bytes()).hexdigest() if args.manifest else None
    args.output.mkdir(parents=True)
    with (args.output / "sequences.jsonl").open("x", encoding="utf-8") as stream:
        for item in examples:
            stream.write(json.dumps(item, allow_nan=False) + "\n")
    report["sequences_sha256"] = hashlib.sha256((args.output / "sequences.jsonl").read_bytes()).hexdigest()
    (args.output / "quality.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
