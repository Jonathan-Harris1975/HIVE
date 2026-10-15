from __future__ import annotations

import asyncio
import datetime as dt
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.core.version import BUILD_STAGE
from app.services import model_registry
from app.services.ops_events import list_ops_events
from app.services.providers.registry import discover_providers
from app.services.repo_health import build_repo_health_report
from app.services.production_manager import build_production_manager_report
from app.services.lifecycle_reports import BUCKET, accept_review_fix, generate_and_store_weekly_report, list_open_reviews, list_weekly_reports
from app.services.repo_hygiene import repo_hygiene_report
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage
from app.storage.sql_store import SqlStore

router = APIRouter(tags=["system"], dependencies=[Depends(require_admin)])


@router.get("/system/repo-hygiene")
def repo_hygiene(
    include_hashes: bool = Query(True),
    max_files: int = Query(5000, ge=100, le=20000),
) -> dict[str, object]:
    """Return duplicate/orphan file candidates for review.

    This endpoint is intentionally read-only. It is useful for HIVE release
    hygiene checks and MAST reporting, but v1.13 never deletes repo files.
    """

    return repo_hygiene_report(include_hashes=include_hashes, max_files=max_files)


@router.get("/system/repo-health")
async def repo_health(
    force_refresh: bool = Query(False),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return compact liveness/readiness status for the governed repo ecosystem."""

    return await build_repo_health_report(settings, force_refresh=force_refresh)


@router.get("/system/production-manager")
async def production_manager(
    force_refresh: bool = Query(False),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return HIVE's authoritative ecosystem production decision."""

    return await build_production_manager_report(settings, force_refresh=force_refresh)


@router.get("/system/lifecycle-reports/current")
def current_lifecycle_report(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Read the immutable current ISO week's HIVE overview from R2."""
    now = dt.datetime.now(dt.timezone.utc).isocalendar()
    key = f"reports/weekly/{now.year}/{now.week:02d}/overview.json"
    try:
        result = R2Storage(settings).read_object(key, max_bytes=2 * 1024 * 1024, bucket=BUCKET)
        report = json.loads(result.content)
        return {"ok": True, "available": True, "report": report, "r2_key": key}
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return {"ok": True, "available": False, "report": None, "r2_key": key, "reason": type(exc).__name__}


@router.post("/system/lifecycle-reports/weekly")
async def generate_weekly_lifecycle_report(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Assemble exact evidence and persist one immutable ISO-week report set."""
    now = dt.datetime.now(dt.timezone.utc).isocalendar()
    return await asyncio.to_thread(generate_and_store_weekly_report, R2Storage(settings), now.year, now.week)


@router.get("/system/lifecycle-reports/history")
def lifecycle_report_history(limit: int = Query(12, ge=1, le=52), settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """List compact summaries for durable weekly overview history."""
    try:
        items = list_weekly_reports(R2Storage(settings), limit)
        return {"ok": True, "count": len(items), "items": items}
    except RuntimeError as exc:
        return {"ok": False, "count": 0, "items": [], "error": type(exc).__name__}


@router.get("/system/lifecycle-reviews")
def lifecycle_reviews(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """List durable HIVE review records that require a decision."""
    try:
        items = list_open_reviews(R2Storage(settings))
        return {"ok": True, "count": len(items), "items": items}
    except RuntimeError as exc:
        return {"ok": False, "count": 0, "items": [], "error": type(exc).__name__}


@router.post("/system/lifecycle-reviews/{review_id}/accept-fix")
async def accept_lifecycle_fix(review_id: str, settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Record an authenticated Accept Fix decision and dispatch the trusted PR repair workflow."""
    try:
        return await asyncio.to_thread(accept_review_fix, R2Storage(settings), settings, review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/system/ops-events")
async def ops_events(
    limit: int = Query(50, ge=1, le=200),
    severity: str | None = Query(None),
    service: str | None = Query(None),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return recent redacted operational events for the HIVE-UI Ops page."""

    return await asyncio.to_thread(
        list_ops_events, settings, limit=limit, severity=severity, service=service
    )


@router.get("/system/runtime-stats")
async def runtime_stats(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return live runtime statistics for the Operations dashboard.

    All values are derived from actual runtime state — never placeholders.
    This endpoint is intentionally limited to system runtime state. Repository
    estate status belongs to /system/repo-health and the Repositories overview,
    preventing Operations from duplicating repository ownership or freshness.
    """
    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    r2 = R2Storage(settings)

    # Model Registry: live category counts from in-process registry
    reg = model_registry.list_categories()
    total_registered_models = sum(len(models) for models in reg.values())
    categories_with_models = [cat for cat, models in reg.items() if models]
    coding_models = model_registry.get_ranked_models("coding")
    default_coding_model = next(
        (model.model_id for model in coding_models if model.score >= settings.model_registry_min_visible_score),
        None,
    )

    # Provider Framework: discover live configured providers
    providers = discover_providers(settings)
    provider_names = [p.name for p in providers]

    # Storage health summary (read-only, non-blocking)
    sql_probe = {"enabled": sql.enabled, "dialect": sql.dialect if sql.enabled else None}
    d1_probe = {"enabled": d1.enabled}
    r2_probe = {
        "enabled": r2.enabled,
        "write_enabled": r2.write_enabled,
        "bucket": settings.cf_r2_bucket or None,
    }

    return {
        "ok": True,
        "build": BUILD_STAGE,
        "sampled_at": time.time(),
        "model_registry": {
            "total_models": total_registered_models,
            "categories_populated": categories_with_models,
            "default_coding_model": default_coding_model,
        },
        "providers": {
            "count": len(providers),
            "names": provider_names,
        },
        "storage": {
            "sql": sql_probe,
            "d1": d1_probe,
            "r2": r2_probe,
        },
        "ops_events": {
            "ingest_enabled": settings.ops_event_ingest_enabled,
            "memory_limit": settings.ops_event_memory_limit,
        },
    }
