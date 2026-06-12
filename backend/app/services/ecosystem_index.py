from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage
from app.storage.sql_store import SqlStore
from app.storage.vectorize import VectorizeClient
from app.services.embeddings import CloudflareEmbeddingsClient


@dataclass(frozen=True)
class EcosystemSearchResult:
    id: str
    lane: str
    source_type: str
    title: str | None
    source_id: str | None
    url: str | None
    score: float
    updated_at: str | None
    metadata: dict[str, Any] | None


def normalise_lane(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower().replace("-", "_") or None


def safe_query(value: str) -> str:
    return " ".join((value or "").strip().split())[:300]


def ecosystem_status(settings: Settings) -> dict[str, object]:
    """Return a lightweight status payload for HIVE/MAST without expensive probes."""

    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    r2 = R2Storage(settings)
    vectorize = VectorizeClient(settings)
    embeddings = CloudflareEmbeddingsClient(settings)
    lanes = settings.r2_ecosystem_lanes
    configured_lanes = [item for item in lanes if item.get("configured")]
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "free_tier": bool(settings.hive_free_tier_mode),
        "services": {
            "postgres": {"configured": sql.enabled, "dialect": sql.dialect if sql.enabled else None},
            "d1": {"configured": d1.enabled, "database_name": settings.d1_database_name or None},
            "r2": {
                "configured": r2.enabled,
                "primary_bucket": settings.cf_r2_bucket or None,
                "configured_lane_count": len(configured_lanes),
                "lane_count": len(lanes),
            },
            "vectorize": {
                "enabled": bool(settings.vectorize_enabled),
                "configured": vectorize.enabled,
                "index_name": settings.vectorize_index_name or None,
            },
            "embeddings": {
                "enabled": bool(settings.embeddings_enabled),
                "configured": embeddings.enabled,
                "provider": settings.embeddings_provider,
                "model": settings.embeddings_model,
                "dimensions": settings.embeddings_dimensions,
            },
            "skills": {
                "lane": "hive_skills",
                "configured": bool(settings.public_url_for_r2_lane("hive_skills", "")),
                "public_base_url": settings.public_url_for_r2_lane("hive_skills", ""),
            },
        },
        "recommended_mast_probe": "/v1/ecosystem/status",
    }


def search_ecosystem_metadata(
    *,
    settings: Settings,
    query: str,
    lane: str | None = None,
    limit: int = 25,
) -> dict[str, object]:
    """Search D1 ecosystem metadata and add public URL hints from R2 lanes."""

    d1 = D1MetadataStore(settings)
    q = safe_query(query)
    clean_lane = normalise_lane(lane)
    if not q:
        return {"ok": False, "error_code": "missing_query", "message": "q is required."}
    result = d1.search_metadata(query=q, lane=clean_lane, limit=limit)
    if not result.get("ok"):
        return result
    items = result.get("items", []) if isinstance(result, dict) else []
    enriched = [_enrich_metadata_item(settings, item, q) for item in items]
    return {
        "ok": True,
        "query": q,
        "lane": clean_lane,
        "count": len(enriched),
        "results": enriched,
        "source": "d1:hive_ecosystem_metadata",
        "note": "Searches indexed ecosystem metadata. R2 lane discovery can be used to populate additional metadata records later.",
    }


def recent_ecosystem_metadata(*, settings: Settings, lane: str | None = None, limit: int = 50) -> dict[str, object]:
    d1 = D1MetadataStore(settings)
    clean_lane = normalise_lane(lane)
    result = d1.list_metadata(lane=clean_lane, limit=limit)
    if not result.get("ok"):
        return result
    items = [_enrich_metadata_item(settings, item, "") for item in result.get("items", [])]
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        grouped[str(item.get("lane") or "unknown")].append(item)
    return {
        "ok": True,
        "lane": clean_lane,
        "count": len(items),
        "items": items,
        "grouped": dict(grouped),
        "source": "d1:hive_ecosystem_metadata",
    }


def skills_search(*, settings: Settings, query: str | None = None, limit: int = 25) -> dict[str, object]:
    """Search/list HIVE skill metadata from D1, with R2 lane hints."""

    q = safe_query(query or "")
    if q:
        payload = search_ecosystem_metadata(settings=settings, query=q, lane="hive_skills", limit=limit)
    else:
        payload = recent_ecosystem_metadata(settings=settings, lane="hive_skills", limit=limit)
    payload["lane_public_base_url"] = settings.public_url_for_r2_lane("hive_skills", "")
    payload["manifest_hint"] = settings.public_url_for_r2_lane("hive_skills", "index/skills-manifest.json")
    payload["note"] = "v1.8 searches imported D1 skill metadata. Run POST /v1/skills/import-manifest to index the R2 shared skill pool."
    return payload


def _enrich_metadata_item(settings: Settings, item: dict[str, Any], query: str) -> dict[str, object]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    lane = normalise_lane(str(item.get("lane") or "")) or "unknown"
    object_key = metadata.get("object_key") or metadata.get("key") or item.get("source_id")
    public_url = item.get("url") or (settings.public_url_for_r2_lane(lane, str(object_key)) if object_key else settings.public_url_for_r2_lane(lane, ""))
    haystack = " ".join(
        str(part or "")
        for part in [item.get("title"), item.get("source_type"), item.get("source_id"), item.get("url"), metadata]
    ).lower()
    terms = [term for term in query.lower().split() if len(term) > 1]
    matched_terms = [term for term in terms if term in haystack]
    score = round((len(matched_terms) / max(1, len(terms))) if terms else 0.0, 3)
    return {
        "id": item.get("id"),
        "lane": lane,
        "source_type": item.get("source_type"),
        "source_id": item.get("source_id"),
        "title": item.get("title"),
        "url": public_url,
        "score": score,
        "matched_terms": matched_terms,
        "updated_at": item.get("updated_at"),
        "created_at": item.get("created_at"),
        "metadata": metadata,
    }
