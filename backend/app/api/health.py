from fastapi import APIRouter

from app.core.config import get_settings
from app.storage.d1 import D1MetadataStore
from app.storage.sql_store import SqlStore

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    sql = SqlStore(settings)
    d1 = D1MetadataStore(settings)
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "r2_configured": bool(settings.r2_endpoint_url and settings.cf_r2_access_key_id),
        "openrouter_configured": bool(settings.openrouter_api_key),
        "vectorize_configured": bool(settings.cf_account_id and settings.cf_api_token),
        "database_configured": sql.enabled,
        "database_dialect": sql.dialect if sql.enabled else None,
        "d1_configured": d1.enabled,
    }
