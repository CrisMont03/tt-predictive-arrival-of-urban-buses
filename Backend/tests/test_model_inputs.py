from copy import deepcopy
from datetime import timedelta

import pytest

from app.model_inputs import (
    FallbackAccumulator, INPUT_WIDTH, bus_context_at, calendar_context, contract,
    fallback_value, fit_scaler, online_inputs, select_observations, timestamp,
    transform, validate_inputs,
)

NOW = timestamp("2026-10-20T14:00:00Z")  # 08:00 in Mexico City


def report(index=1, minutes=10, **changes):
    return {"query_id": f"query-{index:04d}", "uid": "alice", "station_id": "rio_consulado",
            "query_at": NOW - timedelta(minutes=30 + index),
            "received_at": NOW - timedelta(minutes=10),
            "context": {"temp_c": 20, "precipitation_mm": 0, "humidity": 65},
            "actual_min": minutes, "simulation": False, "outside_domain": False, **changes}


def sample(inputs=None, **changes):
    return {"sample_id": "unique-query", "split": "train", "target": 10, "loss_weight": 1,
            "inputs": inputs or online_inputs({"temp_c": 20}, 10, now=NOW), **changes}


def fallback():
    accumulator = FallbackAccumulator()
    accumulator.add("rio_consulado", NOW, 12)
    return accumulator.table


def test_missing_values_stay_masked_and_padding_stays_zero():
    inputs = online_inputs({"temp_c": 20}, 10, now=NOW)
    result = transform(inputs, fit_scaler([sample(inputs)]))
    assert result[:-1] == [[0] * INPUT_WIDTH] * 9
    assert result[-1][7:14] == [1, 1, 1, 1, 0, 1, 0]
    assert result[-1][14:24] == [0] * 10
    assert result[-1][24:] == [0, 0, 1]
    assert inputs["past_wait_mask"][-1] == [0]


def test_real_zero_is_observed_but_no_history_is_not():
    inputs = online_inputs({}, 20, now=NOW, observations=[report(minutes=0)])
    assert inputs["past_wait"][-2] == [0]
    assert inputs["past_wait_mask"][-2] == [1]
    assert inputs["wait_origin"][-2] == [1, 0, 0]
    assert inputs["wait_origin"][-1] == [0, 0, 1]
    assert inputs["bus_mask"] == [[0] * 4] * 20


def test_scaler_rejects_holdout_and_deduplicates_measurements():
    inputs = online_inputs({"temp_c": 20}, 10, now=NOW)
    train = sample(inputs)
    other = sample(online_inputs({"temp_c": 40}, 10, now=NOW), sample_id="other", target=0)
    scaler = fit_scaler([train, train, other])
    assert scaler["temporal"]["mean"][5] == 30
    assert scaler["past_wait"]["mean"] == [5]
    with pytest.raises(ValueError, match="train"):
        fit_scaler([sample(inputs, split="test")])
    assert transform(online_inputs({"temp_c": 100}, 10, now=NOW), scaler)[-1][5] == 7


def test_report_uses_own_wait_only_after_server_received_it():
    delayed = report(received_at=NOW + timedelta(minutes=1))
    early = online_inputs({}, 10, now=NOW, observations=[delayed])
    assert all(value == [0] for value in early["past_wait_mask"])
    later = online_inputs({}, 10, now=NOW + timedelta(minutes=2), observations=[delayed])
    assert later["past_wait"][-2] == [10]
    # Its wait was unknown at its own query, but is known at this prediction.
    assert later["past_wait_mask"][-2] == [1]
    assert later["past_wait_mask"][-1] == [0]


@pytest.mark.parametrize("count", [0, 3, 9, 15])
@pytest.mark.parametrize("window", [10, 20, 30])
def test_sequence_length_backups_and_recent_selection(count, window):
    reports = [report(index=i) for i in range(count)]
    inputs = online_inputs({}, window, now=NOW, observations=reports, fallback=fallback())
    observed = min(count, window - 1)
    assert len(inputs["temporal"]) == window
    assert inputs["wait_origin"].count([1, 0, 0]) == observed
    assert inputs["wait_origin"].count([0, 1, 0]) == window - 1 - observed
    assert inputs["past_wait_mask"][-1] == [0]
    assert len(transform(inputs, fit_scaler([sample()]))[0]) == 27
    times = [timestamp(p["query_at"]) for p in inputs["provenance"]]
    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_filtering_deduplication_domain_station_and_future():
    valid = report()
    invalid = [
        report(index=2, simulation=True), report(index=3, outside_domain=True),
        report(index=4, station_id="other"), report(index=5, minutes=46),
        report(index=6, query_at=NOW), report(index=7, received_at=NOW),
        report(index=8, query_at=NOW - timedelta(hours=3)),
        report(index=9, minutes=float("nan")),
    ]
    result = select_observations([valid, valid, *invalid], NOW, "rio_consulado", 10)
    assert [item["query_id"] for item in result] == [valid["query_id"]]
    # Same qid belonging to another user is not the same query.
    assert len(select_observations([valid, {**valid, "uid": "bob"}], NOW, "rio_consulado", 10)) == 2


def test_fallback_hierarchy_and_original_step_hour():
    accumulator = FallbackAccumulator()
    accumulator.add("rio_consulado", "2026-10-20T12:00:00Z", 5)  # 06:00 Tuesday
    accumulator.add("rio_consulado", "2026-10-20T13:00:00Z", 15)
    assert fallback_value(accumulator.table, "rio_consulado", "2026-10-20T12:15:00Z") == 5
    assert fallback_value(accumulator.table, "rio_consulado", "2026-10-21T12:15:00Z") == 5
    assert fallback_value(accumulator.table, "rio_consulado", NOW) == 10
    assert fallback_value(accumulator.table, "unknown", NOW) is None
    inputs = online_inputs({}, 10, now=NOW, fallback=accumulator.table)
    assert inputs["past_wait"][0] == [5]
    assert inputs["past_wait"][-2] == [15]
    assert inputs["temporal_mask"][0][4:] == [0, 0, 0]
    assert inputs["wait_origin"][0] == [0, 1, 0]


def test_bus_context_never_uses_future_or_equal_time_arrivals():
    arrivals = [NOW - timedelta(minutes=25), NOW - timedelta(minutes=10), NOW, NOW + timedelta(minutes=10)]
    assert bus_context_at(arrivals + arrivals, NOW) == [10, 15, None, None]
    assert bus_context_at(arrivals[:1], NOW - timedelta(minutes=30)) == [None] * 4
    inputs = online_inputs({}, 10, now=NOW, arrivals=arrivals)
    assert inputs["bus_mask"][-1] == [1, 1, 0, 0]


def test_future_reports_cannot_change_past_sequence():
    existing = [report()]
    before = online_inputs({}, 10, now=NOW, observations=existing, fallback=fallback())
    future = report(index=5, received_at=NOW + timedelta(minutes=5))
    after = online_inputs({}, 10, now=NOW, observations=[*existing, future], fallback=fallback())
    assert before == after


def test_coverage_boundary_and_target_guard():
    inputs = online_inputs({}, 10, now=NOW, observations=[report()], fallback=fallback(), start_at=NOW)
    assert inputs["step_mask"] == [[0]] * 9 + [[1]]
    broken = deepcopy(inputs)
    broken["past_wait_mask"][-1] = [1]
    with pytest.raises(ValueError, match="objetivo"):
        validate_inputs(broken, 10)
    with pytest.raises(ValueError, match="Ventana"):
        contract(1)


def test_timezone_and_no_extra_context_features():
    inputs = online_inputs({"traffic_density": .4, "env_alert_active": 1}, 10, now="2026-10-20 08:00:00")
    assert inputs["temporal"][-1] == online_inputs({}, 10, now=NOW)["temporal"][-1]
    assert calendar_context(NOW)["hour"] == 8


def test_masks_and_origin_not_scaled_and_bad_scaler_rejected():
    inputs = online_inputs({}, 10, now=NOW, observations=[report()], fallback=fallback())
    scaler = fit_scaler([sample(inputs)])
    result = transform(inputs, scaler)
    for i in range(10):
        assert result[i][24:] == inputs["wait_origin"][i]
        assert result[i][7:14] == inputs["temporal_mask"][i]
    scaler["past_wait"]["scale"] = [0]
    with pytest.raises(ValueError, match="Escalador"):
        transform(inputs, scaler)
