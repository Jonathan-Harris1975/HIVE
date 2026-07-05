from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.optimisation_engine import (
    OptimisationEngineError,
    list_decisions,
    list_experiments,
    record_decision,
    record_experiment,
    rollback_decision,
    success_rate_report,
)

router = APIRouter(tags=["optimisation-engine"], dependencies=[Depends(require_admin)])


class RecordDecisionRequest(BaseModel):
    decision_type: str = Field(..., min_length=1, max_length=100)
    description: str = Field(..., min_length=1, max_length=1000)
    previous_state: Any = None
    new_state: Any = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)


class RecordExperimentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    hypothesis: str = Field("", max_length=1000)
    outcome: str = Field("", max_length=1000)
    success: bool


@router.post("/optimisation/decisions")
async def post_record_decision(
    body: RecordDecisionRequest, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return record_decision(
        settings,
        decision_type=body.decision_type,
        description=body.description,
        previous_state=body.previous_state,
        new_state=body.new_state,
        confidence=body.confidence,
    )


@router.get("/optimisation/decisions")
async def get_decisions(
    decision_type: str | None = Query(None), settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return {"decisions": list_decisions(settings, decision_type=decision_type)}


@router.post("/optimisation/decisions/{decision_id}/rollback")
async def post_rollback_decision(
    decision_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    try:
        return rollback_decision(settings, decision_id)
    except OptimisationEngineError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/optimisation/experiments")
async def post_record_experiment(
    body: RecordExperimentRequest, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return record_experiment(
        settings, name=body.name, hypothesis=body.hypothesis, outcome=body.outcome, success=body.success
    )


@router.get("/optimisation/experiments")
async def get_experiments(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {"experiments": list_experiments(settings)}


@router.get("/optimisation/stats")
async def get_stats(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return success_rate_report(settings)
