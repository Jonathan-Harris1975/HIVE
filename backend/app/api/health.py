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

    # Derive the top-level flags the HIVE-UI OpsPage reads directly from the
    # /health payload. These must match the HealthResponse TS interface.
    openrouter_configured = bool(settings.openrouter_api_key)
    database_configured = sql.enabled
    r2_configured = r2.enabled
    vectorize_configured = vectorize.enabled
    embeddings_configured = embeddings.enabled
    d1_configured = d1.enabled

    storage_flags = {
        "r2": {
            "enabled": r2.enabled,
            "configured": r2.enabled,
            "write_enabled": r2.write_enabled,
            "read_enabled": r2.read_enabled,
            "bucket_configured": bool(settings.cf_r2_bucket),
            "endpoint_configured": bool(settings.r2_endpoint_url),
            "public_base_url_configured": bool(settings.cf_r2_public_base_url),
            "ecosystem_lane_count": settings.configured_r2_ecosystem_lane_count,
            "ecosystem_lanes_configured": [
                item["lane"] for item in settings.r2_ecosystem_lanes if item.get("configured")
            ],
        },
        "sql": {
            "enabled": bool(settings.database_enabled),
            "configured": sql.enabled,
        },
        "d1": {
            "enabled": bool(settings.d1_enabled),
            "configured": d1.enabled,
        },
        "vectorize": {
            "enabled": bool(settings.vectorize_enabled),
            "configured": vectorize.enabled,
        },
        "embeddings": {
            "enabled": bool(settings.embeddings_enabled),
            "configured": embeddings.enabled,
        },
    }

    # NOTE: this endpoint is intentionally unauthenticated (MAST/uptime checks hit it
    # directly). It reports booleans only — no secrets, no provider/model detail.
    # The top-level *_configured flags are the minimal surface needed by HIVE-UI's
    # OpsPage to render live readiness cards without an authenticated /v1/runtime/readiness call.
    return {
        "ok": True,
        "build": BUILD_STAGE,
        "app": settings.app_name,
        "env": settings.app_env,
        # Top-level flags consumed directly by HIVE-UI OpsPage
        "openrouter_configured": openrouter_configured,
        "database_configured": database_configured,
        "database_dialect": sql.dialect if sql.enabled else None,
        "r2_configured": r2_configured,
        "vectorize_configured": vectorize_configured,
        "vectorize_enabled": bool(settings.vectorize_enabled),
        "embeddings_configured": embeddings_configured,
        "embeddings_enabled": bool(settings.embeddings_enabled),
        "d1_configured": d1_configured,
        "d1_enabled": bool(settings.d1_enabled),
        # Feature flags
        "workflow_presets_enabled": True,
        "r2_ecosystem_lanes_enabled": True,
        "execution_adapters_enabled": bool(execution_adapter_policy(settings)["enabled"]),
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
