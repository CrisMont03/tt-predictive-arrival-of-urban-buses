"""Shared training/inference preprocessing, with explicit missing-data masks."""
from math import sqrt

BRANCHES = (("temporal", "temporal_mask", 7), ("past_wait", "past_wait_mask", 1), ("bus_context", "bus_mask", 4))


def fit_scaler(samples):
    scaler = {}
    for name, mask_name, size in BRANCHES:
        columns = [[] for _ in range(size)]
        for sample in samples:
            # Each event contributes once, not once for every overlapping window.
            values = sample["inputs"][name][-1]
            masks = sample["inputs"][mask_name][-1]
            for i in range(size):
                if masks[i]:
                    columns[i].append(values[i])
        means, scales = [], []
        for values in columns:
            mean = sum(values) / len(values) if values else 0
            variance = sum((value - mean) ** 2 for value in values) / len(values) if values else 0
            means.append(mean); scales.append(sqrt(variance) or 1)
        scaler[name] = {"mean": means, "scale": scales}
    return scaler


def transform(inputs, scaler):
    result = []
    for step, available in enumerate(inputs["step_mask"]):
        vector = []
        for name, mask_name, _ in BRANCHES:
            masks = inputs[mask_name][step]
            stats = scaler[name]
            vector.extend((value - stats["mean"][i]) / stats["scale"][i] if masks[i] and available[0] else 0 for i, value in enumerate(inputs[name][step]))
            vector.extend(masks if available[0] else [0] * len(masks))
        result.append(vector)
    return result


def online_inputs(context, window, previous_wait=None, observations=None):
    values = [context.get(name) for name in ["hour", "day_of_week", "is_weekend", "is_holiday", "precipitation_mm", "temp_c", "humidity"]]
    inputs = {"temporal": [[0] * 7 for _ in range(window - 1)] + [[value if value is not None else 0 for value in values]], "temporal_mask": [[0] * 7 for _ in range(window - 1)] + [[int(value is not None) for value in values]], "past_wait": [[0] for _ in range(window - 1)] + [[previous_wait if previous_wait is not None else 0]], "past_wait_mask": [[0] for _ in range(window - 1)] + [[int(previous_wait is not None)]], "bus_context": [[0] * 4 for _ in range(window)], "bus_mask": [[0] * 4 for _ in range(window)], "step_mask": [[0] for _ in range(window - 1)] + [[1]]}
    # No bus history is inferred from user feedback. Only measurements already
    # received by the server can become the previous-wait branch.
    observations = observations or []
    history = observations[-(window - 1):]
    offset = window - 1 - len(history)
    for i, report in enumerate(history):
        step = online_inputs(report["context"], 1)
        for key in inputs:
            inputs[key][offset + i] = step[key][0]
        earlier = [item for item in observations if item["received_at"] < report["query_at"]]
        if earlier:
            inputs["past_wait"][offset + i] = [max(earlier, key=lambda item: item["received_at"])["actual_min"]]
            inputs["past_wait_mask"][offset + i] = [1]
    if observations:
        inputs["past_wait"][-1] = [max(observations, key=lambda item: item["received_at"])["actual_min"]]
        inputs["past_wait_mask"][-1] = [1]
    return inputs
