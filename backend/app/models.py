from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.model_router import ModelRouter
from app.services.openrouter import OpenRouterClient

router = APIRouter(tags=["models"], dependencies=[Depends(require_admin)])


@router.get("/models")
async def list_models(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    client = OpenRouterClient(settings)
    router_service = ModelRouter(settings)
    raw_models = await client.list_models()
    models = [router_service.summarise_model(model) for model in raw_models]
    return {"count": len(models), "models": models}


@router.post("/models/validate-key")
async def validate_key(settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    client = OpenRouterClient(settings)
    return {"ok": await client.validate_key()}
