from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.providers.registry import discover_providers, provider_health_report

router = APIRouter(tags=["providers"], dependencies=[Depends(require_admin)])


@router.get("/providers")
async def get_providers(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    providers = discover_providers(settings)
    return {"providers": [provider.name for provider in providers], "count": len(providers)}


@router.get("/providers/health")
async def get_providers_health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return await provider_health_report(settings)


@router.get("/providers/{provider_name}/models")
async def get_provider_models(
    provider_name: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    providers = {provider.name: provider for provider in discover_providers(settings)}
    provider = providers.get(provider_name)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown or unconfigured provider: {provider_name}"
        )
    models = await provider.list_models()
    return {
        "provider": provider_name,
        "model_count": len(models),
        "models": [
            {
                "model_id": model.model_id,
                "name": model.name,
                "context_length": model.context_length,
                "pricing_prompt": model.pricing_prompt,
                "pricing_completion": model.pricing_completion,
                "supports_tools": model.supports_tools,
                "supports_structured_output": model.supports_structured_output,
                "input_modalities": model.input_modalities,
                "output_modalities": model.output_modalities,
            }
            for model in models
        ],
    }
