from app.model_inputs import fit_scaler, online_inputs, transform


def test_missing_values_stay_masked_and_padding_stays_zero():
    context = {"hour": 7, "day_of_week": 0, "is_weekend": 0, "is_holiday": 0, "temp_c": 20}
    inputs = online_inputs(context, 10)
    scaler = fit_scaler([{"inputs": inputs}])
    result = transform(inputs, scaler)
    assert result[:-1] == [[0] * 24] * 9
    assert result[-1][7:14] == [1, 1, 1, 1, 0, 1, 0]
    assert result[-1][14:] == [0] * 10


def test_real_zero_is_observed_but_no_history_is_not():
    inputs = online_inputs({"hour": 7}, 20, 0)
    scaler = fit_scaler([{"inputs": inputs}])
    assert transform(inputs, scaler)[-1][15] == 1
    assert online_inputs({"hour": 7}, 20)["past_wait_mask"][-1] == [0]


def test_scaler_only_uses_supplied_training_samples():
    train = online_inputs({"hour": 7, "temp_c": 20}, 10)
    test = online_inputs({"hour": 22, "temp_c": 100}, 10)
    scaler = fit_scaler([{"inputs": train}])
    assert scaler["temporal"]["mean"][5] == 20
    assert transform(test, scaler)[-1][5] == 80


def test_report_label_cannot_appear_before_server_received_it():
    from datetime import datetime, timedelta, timezone
    first = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    observations = [
        {"context": {"hour": 6}, "actual_min": 10, "query_at": first, "received_at": first + timedelta(minutes=20)},
        {"context": {"hour": 6}, "actual_min": 15, "query_at": first + timedelta(minutes=5), "received_at": first + timedelta(minutes=25)},
    ]
    inputs = online_inputs({"hour": 7}, 10, observations=observations)
    assert inputs["past_wait_mask"][-3:-1] == [[0], [0]]
    assert inputs["past_wait"][-1] == [15]
    assert inputs["bus_mask"] == [[0] * 4] * 10
