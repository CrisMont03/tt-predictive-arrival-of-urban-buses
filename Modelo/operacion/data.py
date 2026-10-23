"""Prepare mixed camera/feedback samples, preserving origin and temporal availability."""
from collections import Counter, defaultdict

from app.artifacts import digest, json_bytes
from app.contracts import Context
from app.model_inputs import FallbackAccumulator, MAX_WAIT, SCHEMA_VERSION, contract, online_inputs, timestamp
from Dataset.preparar_modelo import build as camera_build, local_date, row_context


def feedback_rows(reports, cutoff):
    accepted, rejected = {}, Counter()
    cutoff = timestamp(cutoff)
    for report in reports:
        try:
            if report.get("simulation") is not False:
                rejected["simulation_or_unknown_origin"] += 1
                continue
            query_at, received = timestamp(report["query_at"]), timestamp(report["received_at"])
            actual = report["actual_min"]
            if (isinstance(actual, bool) or not isinstance(actual, (float, int)) or not 0 <= actual <= MAX_WAIT
                    or report.get("outside_domain") or report["station_id"] != "rio_consulado"
                    or not query_at <= received < cutoff):
                rejected["domain_or_cutoff"] += 1
                continue
            identity = (report["uid"], report["query_id"])
            if not all(isinstance(v, str) and v for v in identity):
                raise ValueError("Identidad vacía")
            context = Context.model_validate(report["context"]).model_dump()
            row = dict(sample_id="feedback-" + digest(json_bytes(identity)), source="feedback",
                       record_type="user_wait", event_at=query_at.isoformat(), available_at=received.isoformat(),
                       station_id=report["station_id"], context=context, target=float(actual), loss_weight=.5,
                       # received_at means receipt, never a camera bus arrival.
                       query_id=report["query_id"], uid=report["uid"], simulation=False,
                       anomaly_review=actual >= 40)
            if identity in accepted:
                rejected["duplicate"] += 1
                if timestamp(accepted[identity]["available_at"]) <= received:
                    continue
            accepted[identity] = row
        except (KeyError, TypeError, ValueError):
            rejected["malformed"] += 1
    return list(accepted.values()), dict(rejected)


def prepare_mixed(camera_rows, feedback, cutoff, *, window=10, manifest=None, benchmark_ids=()):
    cutoff = timestamp(cutoff)
    # Reuse camera validation/verified continuity; rebuild ALL sequences after new partitions.
    base, _ = camera_build(camera_rows, window, manifest)
    camera = []
    original = {row["sample_id"]: row for row in camera_rows}
    held = set(benchmark_ids)
    held_buses = {(row["station_id"], row["camera_id"], row["video_source"], row["bus_arrival"])
                  for row in base if row["sample_id"] in held}
    excluded = Counter()
    for row in base:
        bus_id = (row["station_id"], row["camera_id"], row["video_source"], row["bus_arrival"])
        if row["sample_id"] in held or bus_id in held_buses:
            excluded["benchmark_camera_group"] += 1
            continue
        if timestamp(row["bus_arrival"]) >= cutoff:
            excluded["camera_after_cutoff"] += 1
            continue
        camera.append({**row, "source": "camera", "available_at": row["bus_arrival"],
                       "context": row_context(original[row["sample_id"]]),
                       "simulation": False})
    reports, rejected = feedback_rows(feedback, cutoff)
    rows = camera + [row for row in reports if row["sample_id"] not in held]
    excluded.update(rejected)
    excluded["benchmark_feedback"] = sum(row["sample_id"] in held for row in reports)
    dates = sorted({local_date(row["available_at"]) for row in rows})
    if len(dates) < 3:
        raise ValueError("Datos insuficientes: se necesitan al menos tres jornadas")
    train_end = min(len(dates) - 2, max(1, int(len(dates) * .7)))
    val_end = min(len(dates) - 1, max(train_end + 1, int(len(dates) * .85)))
    parts = {day: "train" if i < train_end else "validation" if i < val_end else "test" for i, day in enumerate(dates)}
    ends = {}
    for row in rows:
        row["split"] = parts[local_date(row["available_at"])]
        ends[row["split"]] = max(ends.get(row["split"], timestamp(row["available_at"])), timestamp(row["available_at"]))
    groups, starts = defaultdict(list), {}
    verified_starts = {}
    for base_group, coverage_start, _ in (manifest or {}).values():
        verified_starts[base_group] = min(verified_starts.get(base_group, coverage_start), coverage_start)
    for row in rows:
        boundary = ends.get("train") if row["split"] == "validation" else ends.get("validation") if row["split"] == "test" else None
        row["boundary_excluded"] = bool(boundary and timestamp(row["event_at"]) <= boundary)
        row["loss_weight"] = (1.0 if row["source"] == "camera" else .5) if (
            row["record_type"] == "user_wait" and 0 <= row["target"] <= MAX_WAIT and not row["boundary_excluded"]) else 0
        # Feedback is a station stream, not fictitious video coverage. Camera blocks remain isolated.
        base_group = tuple(row["continuity_group"][:-1]) if row["source"] == "camera" else (row["station_id"], "feedback")
        group = (*base_group, row["split"])
        row["continuity_group"] = list(group)
        groups[group].append(row)
        start = verified_starts.get(base_group, timestamp(row["event_at"]))
        if boundary:
            start = max(start, boundary)
        starts[group] = min(starts.get(group, start), start)
    train = sorted((row for row in rows if row["split"] == "train" and row["loss_weight"]),
                   key=lambda row: (timestamp(row["available_at"]), row["sample_id"]))
    frozen = FallbackAccumulator()
    for row in train:
        frozen.add(row["station_id"], row["event_at"], row["target"])
    histories, buses = {}, {}
    for group, events in groups.items():
        histories[group] = [dict(query_id=row["sample_id"], uid=row.get("uid", "camera"),
                                query_at=row["event_at"], received_at=row["available_at"],
                                station_id=row["station_id"], actual_min=row["target"], context=row["context"],
                                simulation=False) for row in events if row["loss_weight"]]
        buses[group] = [row["bus_arrival"] for row in events if row["source"] == "camera" and not row["boundary_excluded"]]
    prefix, position = FallbackAccumulator(), 0
    for row in sorted(rows, key=lambda row: (timestamp(row["event_at"]), row["sample_id"])):
        now = timestamp(row["event_at"])
        while position < len(train) and timestamp(train[position]["available_at"]) < now:
            past = train[position]
            prefix.add(past["station_id"], past["event_at"], past["target"])
            position += 1
        fallback = {} if row["boundary_excluded"] else prefix.table if row["split"] == "train" else frozen.table
        group = tuple(row["continuity_group"])
        common = dict(now=now, station_id=row["station_id"], fallback=fallback, start_at=starts[group],
                      observations=histories[group])
        row["operational_inputs"] = online_inputs(row["context"], window, **common)
        row["cold_inputs"] = online_inputs(row["context"], window, **{**common, "observations": []})
        row["inputs"] = online_inputs(row["context"], window, arrivals=buses[group] if row["source"] == "camera" else None, **common)
    quality = dict(schema_version=SCHEMA_VERSION, window=window, contract=contract(window), fallback=frozen.table,
                   can_train=all(any(r["split"] == part and r["loss_weight"] for r in rows) for part in ("train", "validation", "test")),
                   meets_collection_goal=sum(r["source"] == "camera" and r["loss_weight"] > 0 for r in rows) >= 5000,
                   counts=dict(Counter(row["source"] for row in rows)), exclusions=dict(excluded),
                   training_cutoff=cutoff.isoformat(), simulation=False)
    return rows, quality
