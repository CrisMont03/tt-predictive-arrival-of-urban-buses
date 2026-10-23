"""Explicit development-only predictor; never an LSTM or a training source."""
from datetime import datetime, timezone

from .model_inputs import FallbackAccumulator, contract, fit_scaler, online_inputs, transform


class IntegrationPredictor:
    kind = "integration"
    simulation = True

    def __init__(self, window=10):
        now = datetime(2026, 1, 5, 14, tzinfo=timezone.utc)
        fallback = FallbackAccumulator()
        fallback.add("rio_consulado", now, 12)
        inputs = online_inputs({"temp_c": 20}, window, now=now)
        scaler = fit_scaler([dict(sample_id="integration-only", split="train", target=12,
                                 loss_weight=1, inputs=inputs)])
        self.metadata = dict(model_version="integration-v2-not-an-lstm", window=window,
                             contract=contract(window), scaler=scaler, fallback=fallback.table)
        self.calls = 0
        self.last_inputs = None

    def predict(self, context, observations=None, *, now, station_id="rio_consulado"):
        inputs = online_inputs(context, self.metadata["window"], now=now, station_id=station_id,
                               observations=observations, fallback=self.metadata["fallback"])
        # Exercise the actual v2 transformations, without fitting or training ML.
        transform(inputs, self.metadata["scaler"])
        self.last_inputs = inputs
        self.calls += 1
        waits = [value[0] for value, origin in zip(inputs["past_wait"], inputs["wait_origin"])
                 if origin == [1, 0, 0]]
        minutes = round(sum(waits) / len(waits), 2) if waits else 12.0
        return minutes, (max(0, minutes - 4), min(45, minutes + 4))


async def integration_context():
    """Deterministic provider values, without keys or external requests."""
    return dict(precipitation_mm=0.0, temp_c=20.0, humidity=65.0,
                traffic_density=None, env_alert_active=None)
