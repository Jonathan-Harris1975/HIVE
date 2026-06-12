from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.skill_registry import (
    get_skill_catalogue_item,
    import_skills_manifest,
    list_skills_catalogue,
    search_skills_catalogue,
    skill_categories,
    skills_by_lane,
    skills_by_repo,
    skills_by_risk,
    skills_registry_status,
)

router = APIRouter(tags=["skills"], dependencies=[Depends(require_admin)])


class SkillManifestImportRequest(BaseModel):
    dry_run: bool = True
    limit: int | None = Field(None, ge=1, le=1000)
    search_documents_url: str | None = Field(None, max_length=2048)


@router.get("/skills/status")
def skills_status(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return shared skill pool import/index status."""

    return skills_registry_status(settings)


@router.post("/skills/import-manifest")
def import_manifest(
    payload: SkillManifestImportRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Import R2 shared skill-pool search documents into D1.

    Dry-run is the default so mobile/ReqBin testing can safely confirm the
    manifest shape before writing 200+ D1 metadata records.
    """

    return import_skills_manifest(
        settings=settings,
        dry_run=payload.dry_run,
        limit=payload.limit,
        search_documents_url=payload.search_documents_url,
    )


@router.get("/skills/categories")
def categories(
    limit: int = Query(500, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return category counts from indexed skills."""

    return skill_categories(settings, limit=limit)


@router.get("/skills/search")
def search_skills(
    q: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(25, ge=1, le=100),
    repo: str | None = Query(None, max_length=40),
    hive_lane: str | None = Query(None, max_length=120),
    priority_tier: str | None = Query(None, max_length=80),
    risk_level: str | None = Query(None, max_length=40),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Search the shared HIVE/AIMS/RAMS/Website skills metadata index."""

    return search_skills_catalogue(
        settings=settings,
        query=q,
        limit=limit,
        repo=repo,
        hive_lane=hive_lane,
        priority_tier=priority_tier,
        risk_level=risk_level,
    )


@router.get("/skills/get")
def get_skill(
    id: str = Query(..., min_length=1, max_length=120),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return one skill by S-id, skill: id, slug or title."""

    return get_skill_catalogue_item(settings=settings, skill_id=id)


@router.get("/skills/by-repo")
def by_repo(
    repo: str = Query(..., min_length=1, max_length=40),
    limit: int = Query(100, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List skills mapped to a repo such as HIVE, RAMS, AIMS or Website."""

    return skills_by_repo(settings=settings, repo=repo, limit=limit)


@router.get("/skills/by-risk")
def by_risk(
    risk: str = Query(..., min_length=1, max_length=40),
    limit: int = Query(100, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List skills by risk level for review-gate planning."""

    return skills_by_risk(settings=settings, risk_level=risk, limit=limit)


@router.get("/skills/by-lane")
def by_lane(
    lane: str = Query(..., min_length=1, max_length=120),
    limit: int = Query(100, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List skills by HIVE catalogue lane."""

    return skills_by_lane(settings=settings, hive_lane=lane, limit=limit)


@router.get("/skills/list")
def list_skills(
    limit: int = Query(50, ge=1, le=500),
    repo: str | None = Query(None, max_length=40),
    hive_lane: str | None = Query(None, max_length=120),
    priority_tier: str | None = Query(None, max_length=80),
    risk_level: str | None = Query(None, max_length=40),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List indexed shared skills with optional repo/category filters."""

    return list_skills_catalogue(
        settings=settings,
        limit=limit,
        repo=repo,
        hive_lane=hive_lane,
        priority_tier=priority_tier,
        risk_level=risk_level,
    )
