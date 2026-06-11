from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.ecosystem_index import skills_search

router = APIRouter(tags=["skills"], dependencies=[Depends(require_admin)])


@router.get("/skills/search")
def search_skills(
    q: str = Query(..., min_length=1, max_length=300),
    limit: int = Query(25, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Search the shared HIVE/AIMS/RAMS skills metadata index."""

    return skills_search(settings=settings, query=q, limit=limit)


@router.get("/skills/list")
def list_skills(
    limit: int = Query(50, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List recent shared skills metadata, if indexed in D1."""

    return skills_search(settings=settings, query=None, limit=limit)
