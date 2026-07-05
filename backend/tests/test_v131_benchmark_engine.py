from __future__ import annotations

import json

import pytest

from app.services import benchmark_engine as be


def test_load_weights_returns_defaults_when_empty():
    assert be.load_weights("") == be.DEFAULT_WEIGHTS
    assert be.load_weights("not json") == be.DEFAULT_WEIGHTS


def test_load_weights_merges_overrides_and_ignores_unknown_keys():
    overrides = json.dumps({"coding_benchmark": 0.5, "not_a_real_metric": 99})
    weights = be.load_weights(overrides)
    assert weights["coding_benchmark"] == 0.5
    assert "not_a_real_metric" not in weights
    assert weights["reasoning_benchmark"] == be.DEFAULT_WEIGHTS["reasoning_benchmark"]


def test_normalise_weights_sums_to_one():
    normalised = be.normalise_weights({"coding_benchmark": 2.0, "reasoning_benchmark": 2.0})
    assert sum(normalised.values()) == pytest.approx(1.0)


def test_normalise_weights_rejects_all_zero_weights():
    with pytest.raises(be.BenchmarkEngineError):
        be.normalise_weights({key: 0.0 for key in be.METRIC_KEYS})


def test_score_model_full_metrics_high_score():
    metrics = {key: 1.0 for key in be.METRIC_KEYS}
    result = be.score_model(metrics)
    assert result.score == pytest.approx(1.0)
    assert result.confidence == 1.0


def test_score_model_missing_metrics_default_to_neutral():
    result_empty = be.score_model({})
    assert result_empty.confidence == 0.0
    assert result_empty.score == pytest.approx(0.5)


def test_score_model_clamps_out_of_range_values():
    result = be.score_model({"coding_benchmark": 5.0, "reasoning_benchmark": -3.0})
    assert 0.0 <= result.score <= 1.0
    assert result.metrics["coding_benchmark"] == 1.0
    assert result.metrics["reasoning_benchmark"] == 0.0


def test_rank_models_orders_highest_score_first():
    candidates = [
        {"model_id": "low", "coding_benchmark": 0.1, "reasoning_benchmark": 0.1},
        {"model_id": "high", "coding_benchmark": 0.95, "reasoning_benchmark": 0.9},
        {"model_id": "mid", "coding_benchmark": 0.5, "reasoning_benchmark": 0.5},
    ]
    ranked = be.rank_models(candidates)
    assert [result.model_id for result in ranked] == ["high", "mid", "low"]


def test_rank_models_respects_custom_weights():
    candidates = [
        {"model_id": "cheap-but-poor-coder", "cost": 1.0, "coding_benchmark": 0.1},
        {"model_id": "expensive-great-coder", "cost": 0.0, "coding_benchmark": 1.0},
    ]
    cost_heavy = be.rank_models(candidates, weights={"cost": 1.0})
    assert cost_heavy[0].model_id == "cheap-but-poor-coder"

    coding_heavy = be.rank_models(candidates, weights={"coding_benchmark": 1.0})
    assert coding_heavy[0].model_id == "expensive-great-coder"
