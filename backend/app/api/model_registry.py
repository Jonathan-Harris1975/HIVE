from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.model_registry import (
    CATEGORIES,
    ModelRegistryError,
    get_default_model,
    get_ranked_models,
    list_categories,
    register_model,
    remove_model,
)
from app.storage.d1 import D1MetadataStore

router = APIRouter(tags=["model-registry"], dependencies=[Depends(require_admin)])


class RegisterModelRequest(BaseModel):
    model_id: str = Field(..., min_length=1, max_length=200)
    score: float = Field(..., ge=0.0, le=1000.0)
    provider: str | None = Field(None, max_length=80)
    notes: str | None = Field(None, max_length=500)


@router.get("/model-registry/categories")
async def get_categories() -> dict[str, object]:
    return {"categories": CATEGORIES}


@router.get("/model-registry")
async def get_registry() -> dict[str, object]:
    return {"registry": list_categories()}


@router.get("/model-registry/{category}")
async def get_category_models(category: str) -> dict[str, object]:
    try:
        ranked = get_ranked_models(category)
    except ModelRegistryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {
        "category": category,
        "default_model": ranked[0].model_id if ranked else None,
        "models": [
            {
                "model_id": model.model_id,
                "score": model.score,
                "provider": model.provider,
                "notes": model.notes,
                "registered_at": model.registered_at,
            }
            for model in ranked
        ],
    }


@router.get("/model-registry/{category}/default")
async def get_category_default(category: str) -> dict[str, object]:
    try:
        default_model = get_default_model(category)
    except ModelRegistryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"category": category, "default_model": default_model}


@router.post("/model-registry/{category}")
async def post_register_model(
    category: str,
    body: RegisterModelRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    try:
        ranked = register_model(
            category,
            body.model_id,
            score=body.score,
            provider=body.provider,
            notes=body.notes,
            store=store,
        )
    except ModelRegistryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {
        "category": category,
        "default_model": ranked[0].model_id if ranked else None,
        "model_count": len(ranked),
        "persisted": store.enabled,
    }


@router.delete("/model-registry/{category}/{model_id}")
async def delete_registered_model(
    category: str,
    model_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    try:
        removed = remove_model(category, model_id, store=store)
    except ModelRegistryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{model_id!r} not registered in category {category!r}",
        )
    return {"category": category, "model_id": model_id, "removed": True, "persisted": store.enabled}
