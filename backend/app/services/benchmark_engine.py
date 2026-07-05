from __future__ import annotations

import json
from dataclasses import dataclass

# Phase 6 - Benchmark Engine.
#
# Weighted scoring over a fixed set of normalised (0.0-1.0) metric axes.
# Weights are fully configurable (settings.benchmark_weights_json or an
# explicit override) rather than hard-coded, per the programme's
# "support configurable weighting" requirement. Two metrics (cost, latency)
# are cost-like: lower raw values are better, so callers supply them already
# inverted (1.0 = cheapest/fastest) — inversion policy lives with whoever
# derives the raw metric, not here, since "cheapest" and "fastest" are
# normalised differently depending on the data source.

METRIC_KEYS: tuple[str, ...] = (
    "coding_benchmark",
    "reasoning_benchmark",
    "cost",
    "latency",
    "reliability",
    "long_context",
    "json_reliability",
    "structured_output",
    "community_maturity",
    "internal_historical_performance",
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "coding_benchmark": 0.20,
    "reasoning_benchmark": 0.15,
    "cost": 0.10,
    "latency": 0.10,
    "reliability": 0.15,
    "long_context": 0.08,
    "json_reliability": 0.07,
    "structured_output": 0.07,
    "community_maturity": 0.03,
    "internal_historical_performance": 0.05,
}


class BenchmarkEngineError(ValueError):
    pass


@dataclass(frozen=True)
class BenchmarkResult:
    model_id: str
    score: float
    confidence: float
    metrics: dict[str, float]


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def load_weights(weights_json: str = "") -> dict[str, float]:
    """Merge configured weight overrides onto DEFAULT_WEIGHTS. Unknown keys
    are ignored; missing keys fall back to the default. Malformed JSON
    silently returns the defaults rather than crashing a benchmark run."""
    weights = dict(DEFAULT_WEIGHTS)
    if not weights_json or not weights_json.strip():
        return weights
    try:
        overrides = json.loads(weights_json)
    except (json.JSONDecodeError, ValueError):
        return weights
    if not isinstance(overrides, dict):
        return weights
    for key, value in overrides.items():
        if key in METRIC_KEYS:
            try:
                weights[key] = float(value)
            except (TypeError, ValueError):
                continue
    return weights


def normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        raise BenchmarkEngineError("Benchmark weights must sum to a positive value")
    return {key: max(0.0, value) / total for key, value in weights.items()}


def score_model(metrics: dict[str, float], *, weights: dict[str, float] | None = None) -> BenchmarkResult:
    """Compute a weighted 0.0-1.0 score for a single model's metrics.

    Missing metric keys are treated as 0.5 (neutral) rather than 0.0, so a
    model with partial benchmark coverage isn't unfairly punished versus one
    with complete but middling coverage. `confidence` reflects how many of
    the 10 metric axes were actually supplied.
    """
    resolved_weights = normalise_weights(weights or DEFAULT_WEIGHTS)
    clamped: dict[str, float] = {}
    supplied = 0
    total = 0.0
    for key in METRIC_KEYS:
        if key in metrics and metrics[key] is not None:
            value = _clamp01(float(metrics[key]))
            supplied += 1
        else:
            value = 0.5
        clamped[key] = value
        total += value * resolved_weights.get(key, 0.0)

    model_id = str(metrics.get("model_id", "")) if "model_id" in metrics else ""
    return BenchmarkResult(
        model_id=model_id,
        score=_clamp01(total),
        confidence=supplied / len(METRIC_KEYS),
        metrics=clamped,
    )


def rank_models(
    candidates: list[dict[str, float | str]], *, weights: dict[str, float] | None = None
) -> list[BenchmarkResult]:
    """Score and rank a list of {"model_id": ..., **metrics} dicts,
    highest score first."""
    resolved_weights = normalise_weights(weights or DEFAULT_WEIGHTS)
    results = []
    for candidate in candidates:
        model_id = str(candidate.get("model_id", ""))
        metrics = {key: candidate.get(key) for key in METRIC_KEYS if key in candidate}
        result = score_model(metrics, weights=resolved_weights)
        results.append(BenchmarkResult(model_id=model_id, score=result.score, confidence=result.confidence, metrics=result.metrics))
    results.sort(key=lambda item: item.score, reverse=True)
    return results
