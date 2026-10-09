"""Non-finite benchmark values cannot influence Council percentile scores."""
from __future__ import annotations

from app.services.ai_council import _percentile_scores


def test_nonfinite_indices_are_excluded_from_percentiles():
    rows = [
        {"model_permaslug": f"provider/model-{i}", "coding_index": i}
        for i in range(5)
    ]
    rows.extend([
        {"model_permaslug": "provider/nan", "coding_index": "NaN"},
        {"model_permaslug": "provider/inf", "coding_index": "Infinity"},
        {"model_permaslug": "provider/negative-inf", "coding_index": "-Infinity"},
    ])
    scores = _percentile_scores(rows, "coding_index")
    assert set(scores) == {f"provider/model-{i}" for i in range(5)}
    assert scores["provider/model-0"] == 0.0
    assert scores["provider/model-4"] == 1.0


def test_nonfinite_values_cannot_satisfy_minimum_population():
    rows = [
        {"model_permaslug": "provider/one", "coding_index": 1},
        {"model_permaslug": "provider/two", "coding_index": 2},
        {"model_permaslug": "provider/three", "coding_index": 3},
        {"model_permaslug": "provider/four", "coding_index": 4},
        {"model_permaslug": "provider/nan", "coding_index": float("nan")},
    ]
    assert _percentile_scores(rows, "coding_index") == {}
