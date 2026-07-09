from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.core.version import BUILD_STAGE
from app.services import model_registry
from app.services.ops_events import list_ops_events
from app.services.providers.registry import discover_providers
from app.services.repo_health import build_repo_health_report
from app.services.repo_hygiene import repo_hygiene_report
from app.services.repository_manager import list_repositories, registry_size
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
    This endpoint replaces any hardcoded statistics previously served by
    earlier HIVE builds. Called by HIVE-UI on the Ops overview tab.
    """
    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    r2 = R2Storage(settings)

    # Repository Manager: live count from the in-process registry
    repo_count = registry_size()
    repo_summaries = list_repositories()
    latest_repo_updated = max(
        (r.updated_at for r in repo_summaries), default=None
    )

    # Model Registry: live category counts from in-process registry
    reg = model_registry.list_categories()
    total_registered_models = sum(len(models) for models in reg.values())
    categories_with_models = [cat for cat, models in reg.items() if models]
    default_coding_model = model_registry.get_default_model("coding")

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
        "repository_manager": {
            "registered_count": repo_count,
            "latest_updated_at": latest_repo_updated,
        },
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
