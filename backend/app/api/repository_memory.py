from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repository_memory import (
    HISTORY_FIELDS,
    SCALAR_FIELDS,
    RepositoryMemoryError,
    append_history_entry,
    get_memory_field,
    get_repository_memory,
    search_repository_memory,
    set_memory_field,
)
from app.storage.ai_search import AiSearchClient
from app.storage.d1 import D1MetadataStore

router = APIRouter(tags=["repository-memory"], dependencies=[Depends(require_admin)])


class SetMemoryFieldRequest(BaseModel):
    content: Any


class AppendHistoryEntryRequest(BaseModel):
    entry: dict[str, Any]


@router.get("/repositories/{repository_id}/memory")
async def get_memory(
    repository_id: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    return {
        "repository_id": repository_id,
        "fields": {
            "scalar": SCALAR_FIELDS,
            "history": HISTORY_FIELDS,
        },
        "memory": get_repository_memory(store, repository_id=repository_id),
    }


@router.get("/repositories/{repository_id}/memory/{field_name}")
async def get_memory_field_endpoint(
    repository_id: str, field_name: str, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    try:
        result = get_memory_field(store, repository_id=repository_id, field_name=field_name)
    except RepositoryMemoryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    if result is None:
        return {"repository_id": repository_id, "field_name": field_name, "content": None, "updated_at": None}
    return {
        "repository_id": result.repository_id,
        "field_name": result.field_name,
        "content": result.content,
        "updated_at": result.updated_at,
    }


@router.put("/repositories/{repository_id}/memory/{field_name}")
async def put_memory_field(
    repository_id: str,
    field_name: str,
    body: SetMemoryFieldRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    try:
        result = set_memory_field(
            store, repository_id=repository_id, field_name=field_name, content=body.content
        )
    except RepositoryMemoryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"repository_id": repository_id, "field_name": field_name, "result": result}


@router.post("/repositories/{repository_id}/memory/{field_name}/append")
async def post_memory_history_append(
    repository_id: str,
    field_name: str,
    body: AppendHistoryEntryRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    try:
        result = append_history_entry(
            store, repository_id=repository_id, field_name=field_name, entry=body.entry
        )
    except RepositoryMemoryError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return {"repository_id": repository_id, "field_name": field_name, "result": result}


@router.get("/repositories/{repository_id}/memory-search")
async def get_memory_search(
    repository_id: str,
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    store = D1MetadataStore(settings)
    return search_repository_memory(store, query=q, repository_id=repository_id, limit=limit)


@router.get("/repository-memory/ai-search/diagnostics")
async def get_ai_search_diagnostics(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return await AiSearchClient(settings).diagnostics()


@router.get("/repository-memory/ai-search")
async def get_ai_search_query(
    q: str = Query(..., min_length=1, max_length=500),
    top_k: int | None = Query(None, ge=1, le=50),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    return await AiSearchClient(settings).search(q, top_k=top_k)
