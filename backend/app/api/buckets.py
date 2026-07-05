from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.services.bucket_manager import list_accessible_buckets

router = APIRouter(tags=["buckets"], dependencies=[Depends(require_admin)])


@router.get("/buckets")
async def get_buckets() -> dict[str, object]:
    return {"buckets": list_accessible_buckets()}
