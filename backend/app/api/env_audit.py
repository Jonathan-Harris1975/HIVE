from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.security import require_admin
from app.services.env_audit import audit_environment

router = APIRouter(tags=["environment"], dependencies=[Depends(require_admin)])


@router.get("/environment/audit")
async def get_environment_audit() -> dict[str, object]:
    return audit_environment()
