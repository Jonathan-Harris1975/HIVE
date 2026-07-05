from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.security import require_admin
from app.services.benchmark_engine import METRIC_KEYS, rank_models

router = APIRouter(tags=["benchmark"], dependencies=[Depends(require_admin)])


class BenchmarkCandidate(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=200)
    coding_benchmark: float | None = None
    reasoning_benchmark: float | None = None
    cost: float | None = None
    latency: float | None = None
    reliability: float | None = None
    long_context: float | None = None
    json_reliability: float | None = None
    structured_output: float | None = None
    community_maturity: float | None = None
    internal_historical_performance: float | None = None


class BenchmarkRequest(BaseModel):
    candidates: list[BenchmarkCandidate]
    weights: dict[str, float] | None = None


@router.get("/benchmark/metrics")
async def get_metric_keys() -> dict[str, object]:
    return {"metric_keys": METRIC_KEYS}


@router.post("/benchmark/rank")
async def post_rank_models(body: BenchmarkRequest) -> dict[str, object]:
    candidates = [candidate.model_dump(exclude_none=True) for candidate in body.candidates]
    ranked = rank_models(candidates, weights=body.weights)
    return {
        "ranking": [
            {
                "model_id": result.model_id,
                "score": result.score,
                "confidence": result.confidence,
                "metrics": result.metrics,
            }
            for result in ranked
        ]
    }
