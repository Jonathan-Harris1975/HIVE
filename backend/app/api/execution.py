from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.execution_reviews import (
    create_execution_review_plan,
    decide_execution_review_plan,
    get_execution_review_plan,
    list_execution_review_plans,
)

router = APIRouter(tags=["execution-reviews"], dependencies=[Depends(require_admin)])


class ExecutionReviewCreateRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1200)
    repo: str | None = Field(None, max_length=40)
    workflow_preset: str | None = Field(None, max_length=120)
    requested_by: str | None = Field(None, max_length=120)
    limit: int = Field(5, ge=1, le=25)
    dry_run: bool = True


class ExecutionReviewDecisionRequest(BaseModel):
    decision: str = Field(..., min_length=1, max_length=40)
    reviewer: str | None = Field(None, max_length=120)
    note: str | None = Field(None, max_length=1000)


@router.post("/execution-reviews")
def create_review(
    payload: ExecutionReviewCreateRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Create a review-gated execution plan record.

    Dry-run defaults to true. Live creation stores the review plan in D1 so the
    future UI can show an approval queue. This never executes a skill.
    """

    return create_execution_review_plan(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        workflow_preset=payload.workflow_preset,
        requested_by=payload.requested_by,
        limit=payload.limit,
        dry_run=payload.dry_run,
    )


@router.get("/execution-reviews")
def list_reviews(
    status: str | None = Query(None, max_length=40),
    repo: str | None = Query(None, max_length=40),
    limit: int = Query(50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List stored execution review plans."""

    return list_execution_review_plans(settings=settings, status=status, repo=repo, limit=limit)


@router.get("/execution-reviews/{plan_id}")
def get_review(plan_id: str, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return one stored execution review plan."""

    return get_execution_review_plan(settings=settings, plan_id=plan_id)


@router.post("/execution-reviews/{plan_id}/decision")
def decide_review(
    plan_id: str,
    payload: ExecutionReviewDecisionRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Record a review decision without executing anything."""

    return decide_execution_review_plan(
        settings=settings,
        plan_id=plan_id,
        decision=payload.decision,
        reviewer=payload.reviewer,
        note=payload.note,
    )
