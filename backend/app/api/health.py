from fastapi import APIRouter

from app.core.config import get_settings
from app.core.version import BUILD_STAGE
from app.services.embeddings import CloudflareEmbeddingsClient
from app.services.execution_adapters import execution_adapter_policy
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage
from app.storage.sql_store import SqlStore
from app.storage.vectorize import VectorizeClient

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    r2 = R2Storage(settings)
    vectorize = VectorizeClient(settings)
    embeddings = CloudflareEmbeddingsClient(settings)
    storage_flags = {
        "r2": {
            "configured": r2.enabled,
            "bucket_configured": bool(settings.cf_r2_bucket),
            "endpoint_configured": bool(settings.r2_endpoint_url),
            "public_base_url_configured": bool(settings.cf_r2_public_base_url),
            "ecosystem_lane_count": settings.configured_r2_ecosystem_lane_count,
            "ecosystem_lanes_configured": [
                item["lane"] for item in settings.r2_ecosystem_lanes if item.get("configured")
            ],
        },
        "sql": {
            "configured": sql.enabled,
            "dialect": sql.dialect if sql.enabled else None,
        },
        "d1": {
            "configured": d1.enabled,
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
    }
    return {
        "ok": True,
        "build": BUILD_STAGE,
        "app": settings.app_name,
        "env": settings.app_env,
        "workflow_presets_enabled": True,
        "r2_ecosystem_lanes_enabled": True,
        "execution_adapters_enabled": bool(execution_adapter_policy(settings)["enabled"]),
        "execution_adapter_policy": execution_adapter_policy(settings),
        "free_tier": {
            "enabled": settings.hive_free_tier_mode,
            "platform": "koyeb-free-web-service" if settings.hive_free_tier_mode else None,
            "ingestion_limits": {
                "document_extract_max_chars": settings.document_extract_max_chars,
                "zip_extract_max_members": settings.zip_extract_max_members,
                "zip_extract_max_member_bytes": settings.zip_extract_max_member_bytes,
                "zip_extract_max_total_text_chars": settings.zip_extract_max_total_text_chars,
                "zip_extract_max_depth": settings.zip_extract_max_depth,
            },
        },
        "storage_flags": storage_flags,
        "r2_configured": r2.enabled,
        "openrouter_configured": bool(settings.openrouter_api_key),
        "vectorize_configured": vectorize.enabled,
        "vectorize_enabled": bool(settings.vectorize_enabled),
        "embeddings_configured": embeddings.enabled,
        "embeddings_enabled": bool(settings.embeddings_enabled),
        "database_configured": sql.enabled,
        "database_dialect": sql.dialect if sql.enabled else None,
        "d1_configured": d1.enabled,
    }


@router.get("/healthz")
async def mast_keepawake_health() -> dict[str, object]:
    """Very small unauthenticated health point for MAST keep-awake checks."""

    settings = get_settings()
    return {
        "ok": True,
        "app": settings.app_name,
        "build": BUILD_STAGE,
        "free_tier": settings.hive_free_tier_mode,
    }
