from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.workflow_graphs import (
    build_workflow_graph,
    controlled_execution_preview,
    execution_preview_policies,
    workflow_graph_templates,
)

router = APIRouter(tags=["workflow-graphs"], dependencies=[Depends(require_admin)])


class WorkflowGraphBuildRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1200)
    repo: str | None = Field(None, max_length=40)
    workflow_preset: str | None = Field(None, max_length=120)
    template: str | None = Field(None, max_length=80)
    limit: int = Field(5, ge=1, le=25)


class ExecutionPreviewRequest(WorkflowGraphBuildRequest):
    approval_state: str | None = Field(None, max_length=40)


@router.get("/workflow-graphs/templates")
def templates() -> dict[str, object]:
    """Return workflow graph templates for UI/planning use."""

    return workflow_graph_templates()


@router.post("/workflow-graphs/build")
def build_graph(
    payload: WorkflowGraphBuildRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Build a graph-shaped review plan without executing anything."""

    return build_workflow_graph(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        workflow_preset=payload.workflow_preset,
        template=payload.template,
        limit=payload.limit,
    )


@router.get("/execution-preview/policies")
def preview_policies() -> dict[str, object]:
    """Return the controlled-preview policy map."""

    return execution_preview_policies()


@router.post("/execution-preview")
def preview_execution(
    payload: ExecutionPreviewRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Preview controlled execution status without running adapters."""

    return controlled_execution_preview(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        workflow_preset=payload.workflow_preset,
        template=payload.template,
        limit=payload.limit,
        approval_state=payload.approval_state,
    )
