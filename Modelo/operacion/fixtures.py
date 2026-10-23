"""End-to-end process fixture. Never authenticates, trains Keras, or writes to GCP."""
from types import SimpleNamespace

from app.artifacts import immutable_json
from app.model_inputs import SCHEMA_VERSION
from Modelo.operacion.evaluation import compare
from Modelo.operacion.registry import LocalRegistry


def run_fixture(directory, scenario="better"):
    from Modelo.retrain_job import cycle
    from pathlib import Path
    directory = Path(directory)
    benchmark = dict(format="arribo-benchmark-v1", purpose="monthly", simulation=True, cases=[
        dict(sample_id="fixture-heldout", station_id="rio_consulado", query_at="2026-10-20T15:00:00Z",
             context=dict(hour=9, day_of_week=1, is_weekend=0, is_holiday=0), target=12, simulation=True)])
    meta = dict(schema_version=SCHEMA_VERSION, model_version="fixture-not-an-lstm", simulation=True,
                training_sample_ids=["fixture-train"], calibration_sample_ids=["fixture-validation"],
                training_cutoff="2026-10-19T00:00:00Z", eligible=False, approved=False)
    incumbent = SimpleNamespace(metadata={**meta, "model_version": "fixture-incumbent"}, predict=lambda *a, **k: (16, (0, 45)))
    values = dict(better=13, equal=16, worse=20, invalid=float("nan"), insufficient=13)

    def train(prepared, output, run_id):
        immutable_json(output / "metadata.json", meta)
        return SimpleNamespace(metadata=meta, predict=lambda *a, **k: (values[scenario], (0, 45)))

    return cycle(LocalRegistry(directory / "registry"), directory / scenario,
                 run_id="fixture-" + scenario, inputs=dict(simulation=True, scenario=scenario),
                 prepare=lambda inputs: ([{"sample_id": "fixture-train", "simulation": True}],
                                         dict(can_train=scenario != "insufficient", simulation=True)),
                 train=train, evaluate=lambda candidate: compare(benchmark, candidate, incumbent, synthetic=True))
