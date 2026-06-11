from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.ecosystem_index import (
    ecosystem_status,
    recent_ecosystem_metadata,
    search_ecosystem_metadata,
)
from app.storage.r2 import R2Storage
from app.storage.vectorize import VectorizeClient
from app.services.embeddings import CloudflareEmbeddingsClient
from app.storage.sql_store import SqlStore
from app.storage.d1 import D1MetadataStore

router = APIRouter(tags=["ecosystem"], dependencies=[Depends(require_admin)])


@router.get("/ecosystem/status")
async def status(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """MAST-friendly ecosystem health/status endpoint."""

    return ecosystem_status(settings)


@router.get("/ecosystem/search")
def search(
    q: str = Query(..., min_length=1, max_length=300),
    lane: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(25, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Search D1 ecosystem metadata across audits/blog/podcast/skills lanes."""

    return search_ecosystem_metadata(settings=settings, query=q, lane=lane, limit=limit)


@router.get("/ecosystem/recent")
def recent(
    lane: str | None = Query(None, min_length=1, max_length=80),
    limit: int = Query(50, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Return recent ecosystem metadata records grouped by lane."""

    return recent_ecosystem_metadata(settings=settings, lane=lane, limit=limit)


@router.get("/files/r2-discovery")
def r2_discovery(
    lane: str | None = Query(None, min_length=1, max_length=80),
    prefix: str = Query("", max_length=512),
    limit: int = Query(25, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Lightweight R2 lane discovery.

    This is intentionally count/preview focused for Koyeb Free. It does not read
    object bodies and it tolerates lane-level permission failures.
    """

    storage = R2Storage(settings)
    lanes = settings.r2_ecosystem_lanes
    clean_lane = lane.strip().lower().replace("-", "_") if lane else None
    selected = [item for item in lanes if item.get("configured") and (not clean_lane or item["lane"] == clean_lane)]
    discoveries = []
    for item in selected:
        bucket = item.get("bucket")
        if not bucket:
            discoveries.append({"lane": item["lane"], "ok": False, "error": "bucket_not_configured"})
            continue
        try:
            objects = storage.list_objects(prefix=prefix, limit=limit, bucket=str(bucket), public_base_url=item.get("public_base_url"))
            discoveries.append({
                "lane": item["lane"],
                "bucket": bucket,
                "ok": True,
                "prefix": prefix,
                "preview_count": len(objects),
                "limit": limit,
                "objects_preview": [obj.__dict__ for obj in objects],
                "note": "preview_count is limited; this is discovery, not a full bucket inventory.",
            })
        except Exception as exc:  # pragma: no cover - depends on live Cloudflare permissions
            discoveries.append({"lane": item["lane"], "bucket": bucket, "ok": False, "error": str(exc)})
    return {
        "ok": True,
        "lane": clean_lane,
        "prefix": prefix,
        "count": len(discoveries),
        "discoveries": discoveries,
        "free_tier_note": "Bounded previews only; no content reads or large bucket walks.",
    }
