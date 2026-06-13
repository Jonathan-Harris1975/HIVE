from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.workflow_graphs import (
    execution_policy_profiles,
    get_saved_execution_preview,
    list_saved_execution_previews,
    save_execution_preview,
    simulate_workflow_execution,
)
from app.services.execution_reviews import (
    create_execution_review_plan,
    decide_execution_review_plan,
    execution_review_audit_trail,
    execution_review_evidence_pack,
    export_execution_review_pack,
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


class ExecutionReviewExportRequest(BaseModel):
    format: str = Field("json", min_length=2, max_length=20)


class ExecutionPreviewSaveRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1200)
    repo: str | None = Field(None, max_length=40)
    workflow_preset: str | None = Field(None, max_length=120)
    template: str | None = Field(None, max_length=80)
    approval_state: str | None = Field(None, max_length=40)
    policy_profile: str | None = Field(None, max_length=80)
    requested_by: str | None = Field(None, max_length=120)
    limit: int = Field(5, ge=1, le=25)
    dry_run: bool = False


class WorkflowSimulationRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1200)
    repo: str | None = Field(None, max_length=40)
    workflow_preset: str | None = Field(None, max_length=120)
    template: str | None = Field(None, max_length=80)
    approval_state: str | None = Field(None, max_length=40)
    policy_profile: str | None = Field(None, max_length=80)
    limit: int = Field(5, ge=1, le=25)


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


@router.get("/execution-reviews/{plan_id}/audit-trail")
def review_audit_trail(plan_id: str, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return the decision timeline for one execution review."""

    return execution_review_audit_trail(settings=settings, plan_id=plan_id)


@router.get("/execution-reviews/{plan_id}/evidence-pack")
def review_evidence_pack(plan_id: str, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return a UI/export friendly evidence pack for one execution review."""

    return execution_review_evidence_pack(settings=settings, plan_id=plan_id)


@router.post("/execution-reviews/{plan_id}/export")
def export_review_evidence_pack(
    plan_id: str,
    payload: ExecutionReviewExportRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return an inline JSON or Markdown evidence-pack export."""

    return export_execution_review_pack(settings=settings, plan_id=plan_id, export_format=payload.format)


@router.get("/execution-preview/policy-profiles")
def execution_preview_policy_profiles() -> dict[str, object]:
    """Return reusable policy profiles for preview and simulation gates."""

    return execution_policy_profiles()


@router.post("/execution-preview/save")
def save_preview(
    payload: ExecutionPreviewSaveRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Persist a controlled execution preview in D1 without executing anything."""

    return save_execution_preview(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        workflow_preset=payload.workflow_preset,
        template=payload.template,
        limit=payload.limit,
        approval_state=payload.approval_state,
        requested_by=payload.requested_by,
        policy_profile=payload.policy_profile,
        dry_run=payload.dry_run,
    )


@router.get("/execution-preview/history")
def execution_preview_history(
    repo: str | None = Query(None, max_length=40),
    policy_profile: str | None = Query(None, max_length=80),
    limit: int = Query(50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List saved controlled execution previews from D1."""

    return list_saved_execution_previews(settings=settings, repo=repo, policy_profile=policy_profile, limit=limit)


@router.get("/execution-preview/{preview_id}")
def get_execution_preview(preview_id: str, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return one saved controlled execution preview."""

    return get_saved_execution_preview(settings=settings, preview_id=preview_id)


@router.post("/workflow-simulation")
def workflow_simulation(
    payload: WorkflowSimulationRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Run a deterministic pretend-mode simulation of a workflow plan."""

    return simulate_workflow_execution(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        workflow_preset=payload.workflow_preset,
        template=payload.template,
        limit=payload.limit,
        approval_state=payload.approval_state,
        policy_profile=payload.policy_profile,
    )
