from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.skill_registry import (
    get_skill_catalogue_item,
    import_skills_manifest,
    list_skills_catalogue,
    recommend_skills,
    rebuild_skills_index,
    route_skill_request,
    search_skills_catalogue,
    shared_execution_plan,
    skill_categories,
    skill_registry_duplicates,
    skill_registry_integrity_report,
    skill_registry_missing,
    skill_registry_orphans,
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


class SkillIndexRebuildRequest(BaseModel):
    dry_run: bool = True
    limit: int | None = Field(None, ge=1, le=1000)


class SkillRecommendationRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1000)
    repo: str | None = Field(None, max_length=40)
    hive_lane: str | None = Field(None, max_length=120)
    risk_ceiling: str | None = Field(None, max_length=40)
    limit: int = Field(10, ge=1, le=50)


class SkillRouteRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1000)
    repo: str | None = Field(None, max_length=40)
    hive_lane: str | None = Field(None, max_length=120)
    limit: int = Field(5, ge=1, le=25)


class SharedExecutionPlanRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=1200)
    repo: str | None = Field(None, max_length=40)
    workflow_preset: str | None = Field(None, max_length=120)
    limit: int = Field(5, ge=1, le=25)


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


@router.get("/skills/integrity")
def integrity(
    limit: int = Query(500, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return read-only skill-registry integrity and drift checks."""

    return skill_registry_integrity_report(settings=settings, limit=limit)


@router.get("/skills/duplicates")
def duplicates(
    limit: int = Query(500, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return duplicate skill IDs, slugs, object keys and search-document IDs."""

    return skill_registry_duplicates(settings=settings, limit=limit)


@router.get("/skills/missing")
def missing(
    limit: int = Query(500, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return missing required skill fields and invalid taxonomy values."""

    return skill_registry_missing(settings=settings, limit=limit)


@router.get("/skills/orphans")
def orphans(
    limit: int = Query(500, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return likely orphan/mismatched skill registry records without deleting anything."""

    return skill_registry_orphans(settings=settings, limit=limit)


@router.post("/skills/rebuild-index")
def rebuild_index(
    payload: SkillIndexRebuildRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Rebuild the D1 skill index from the R2 shared search-document manifest."""

    return rebuild_skills_index(settings=settings, dry_run=payload.dry_run, limit=payload.limit)


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


@router.post("/skills/recommend")
def recommend(
    payload: SkillRecommendationRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Recommend registry skills for a task without executing anything."""

    return recommend_skills(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        hive_lane=payload.hive_lane,
        risk_ceiling=payload.risk_ceiling,
        limit=payload.limit,
    )


@router.post("/skills/route")
def route(
    payload: SkillRouteRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Create a review-gated skill routing plan for a task."""

    return route_skill_request(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        hive_lane=payload.hive_lane,
        limit=payload.limit,
    )


@router.post("/ecosystem/execution-plan")
def execution_plan(
    payload: SharedExecutionPlanRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return a shared ecosystem execution plan; does not mutate systems."""

    return shared_execution_plan(
        settings=settings,
        task=payload.task,
        repo=payload.repo,
        workflow_preset=payload.workflow_preset,
        limit=payload.limit,
    )
