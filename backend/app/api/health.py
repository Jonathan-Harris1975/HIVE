from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "ok": True,
        "app": settings.app_name,
        "env": settings.app_env,
        "r2_configured": bool(settings.r2_endpoint_url and settings.cf_r2_access_key_id),
        "openrouter_configured": bool(settings.openrouter_api_key),
        "vectorize_configured": bool(settings.cf_account_id and settings.cf_api_token),
    }
