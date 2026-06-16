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
    visible_models = [model for model in models if model.get("visible_in_chat_picker")]
    return {
        "count": len(models),
        "visible_count": len(visible_models),
        "models": models,
        "groups": router_service.model_group_manifest(visible_models),
        "policy": {
            "image_generation": "discovery_only",
            "video_generation": "discovery_only",
            "standard_chat_requires_text_output": True,
        },
    }


@router.post("/models/validate-key")
async def validate_key(settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    client = OpenRouterClient(settings)
    return {"ok": await client.validate_key()}
