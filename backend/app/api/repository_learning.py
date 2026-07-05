from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_learning import (
    record_coding_pattern,
    record_patch_outcome,
    record_preferred_model,
    update_project_dna,
)

router = APIRouter(tags=["repository-learning"], dependencies=[Depends(require_admin)])


class PatchOutcomeRequest(BaseModel):
    summary: str = Field(..., min_length=1, max_length=1000)
    success: bool
    files_changed: list[str] = Field(default_factory=list)


class CodingPatternRequest(BaseModel):
    pattern: str = Field(..., min_length=1, max_length=500)
    context: str = Field("", max_length=1000)


class PreferredModelRequest(BaseModel):
    category: str = Field(..., min_length=1, max_length=50)
    model_id: str = Field(..., min_length=1, max_length=200)
    reason: str = Field("", max_length=500)


@router.post("/repositories/{repository_id}/learning/patch-outcome")
async def post_patch_outcome(
    repository_id: str, body: PatchOutcomeRequest, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return record_patch_outcome(
        settings,
        repository_id=repository_id,
        summary=body.summary,
        success=body.success,
        files_changed=body.files_changed,
    )


@router.post("/repositories/{repository_id}/learning/coding-pattern")
async def post_coding_pattern(
    repository_id: str, body: CodingPatternRequest, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return record_coding_pattern(
        settings, repository_id=repository_id, pattern=body.pattern, context=body.context
    )


@router.post("/repositories/{repository_id}/learning/preferred-model")
async def post_preferred_model(
    repository_id: str, body: PreferredModelRequest, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return record_preferred_model(
        settings,
        repository_id=repository_id,
        category=body.category,
        model_id=body.model_id,
        reason=body.reason,
    )


@router.post("/repositories/{repository_id}/learning/refresh-project-dna")
async def post_refresh_project_dna(
    repository_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return update_project_dna(settings, repository_id=repository_id)
