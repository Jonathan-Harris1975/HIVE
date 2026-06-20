from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.storage.d1 import D1MetadataStore
from app.services.skill_registry import (
    cleanup_uploaded_file_skill_records,
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


class UploadedFileSkillCleanupRequest(BaseModel):
    dry_run: bool = True
    limit: int = Field(500, ge=1, le=1000)
    confirm: str | None = Field(None, max_length=80)


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


class SkillFromFileRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=180)
    object_key: str = Field(..., min_length=1, max_length=2048)
    source_lane: str = Field("uploads", min_length=1, max_length=80)
    description: str | None = Field(None, max_length=1200)
    repo: str | None = Field("HIVE", max_length=40)
    hive_lane: str | None = Field("uploaded-file-skills", max_length=120)
    priority_tier: str = Field("P2", min_length=1, max_length=40)
    risk_level: str = Field("medium", min_length=1, max_length=40)
    tags: list[str] = Field(default_factory=lambda: ["uploaded-file"], max_length=20)
    dry_run: bool = False


def _is_hive_skills_descriptor_source(source_lane: str, object_key: str) -> bool:
    lane = (source_lane or "").strip().lower().replace("-", "_")
    if lane in {"skill", "skills"}:
        lane = "hive_skills"
    key = (object_key or "").replace("\\", "/").lstrip("/")
    return lane == "hive_skills" and key.startswith("skills/")


@router.post("/skills/from-file")
def skill_from_file(
    payload: SkillFromFileRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Register a reviewed descriptor from the HIVE skills folder in D1."""

    if not _is_hive_skills_descriptor_source(payload.source_lane, payload.object_key):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Create skill from file is only available for descriptor files selected from the hive_skills lane under skills/.",
                "error_code": "skill_source_not_hive_skills_folder",
                "required_lane": "hive_skills",
                "required_prefix": "skills/",
            },
        )

    d1 = D1MetadataStore(settings)
    skill_id = f"upload-{uuid.uuid4().hex[:12]}"
    slug = _slugify(payload.title)
    title = payload.title.strip()
    source_url = settings.public_url_for_r2_lane(payload.source_lane, payload.object_key)
    tags = [str(tag).strip() for tag in payload.tags if str(tag).strip()][:20]
    if "uploaded-file" not in [tag.lower() for tag in tags]:
        tags.insert(0, "uploaded-file")
    repo = (payload.repo or "HIVE").strip()
    description = (payload.description or "").strip()
    metadata = {
        "skill_id": skill_id,
        "reference_prefix": skill_id,
        "slug": slug,
        "name": title,
        "description": description,
        "object_key": payload.object_key,
        "descriptor_url": source_url,
        "search_document_id": f"skill:{skill_id}",
        "priority_tier": payload.priority_tier,
        "hive_lane": payload.hive_lane or "uploaded-file-skills",
        "risk_level": payload.risk_level,
        "repos": [repo] if repo else [],
        "tags": tags,
        "catalogue_category": payload.hive_lane or "uploaded-file-skills",
        "indexable_text": " ".join(
            part for part in [title, description, payload.object_key, repo, " ".join(tags)] if part
        ),
        "source_register": "HIVE reviewed skills-folder descriptor registration",
        "source_manifest_key": None,
        "source_lane": payload.source_lane,
        "source_object_key": payload.object_key,
        "created_from_hive_skills_folder_file": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    item = {
        "id": f"skill:{skill_id}",
        "source_id": skill_id,
        "title": title,
        "url": source_url,
        "metadata": metadata,
    }
    if payload.dry_run:
        return {"ok": True, "dry_run": True, "enabled": d1.enabled, "skill": item}
    if not d1.enabled:
        return {
            "ok": False,
            "error_code": "d1_disabled",
            "message": "D1 metadata store is not configured, so the reviewed skill could not be added to the catalogue.",
            "d1": d1.safe_config(),
            "skill": item,
        }
    result = d1.upsert_metadata(
        item_id=item["id"],
        lane="hive_skills",
        source_type="skill_descriptor",
        source_id=skill_id,
        title=title,
        url=source_url,
        metadata=metadata,
    )
    return {
        "ok": bool(result.get("ok")),
        "enabled": True,
        "skill": item,
        "d1_result": result,
        "message": "Reviewed file skill registered." if result.get("ok") else "D1 write failed.",
    }




@router.post("/skills/cleanup-uploaded-file-skills")
def cleanup_uploaded_file_skills(
    payload: UploadedFileSkillCleanupRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Preview or delete legacy ordinary-file skill records from D1 only."""

    return cleanup_uploaded_file_skill_records(
        settings=settings,
        dry_run=payload.dry_run,
        limit=payload.limit,
        confirm=payload.confirm,
    )

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


def _slugify(value: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return clean[:100] or "uploaded-file-skill"
