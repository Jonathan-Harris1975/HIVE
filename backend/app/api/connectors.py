from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.connectors.registry import all_connector_reports

router = APIRouter(tags=["connectors"], dependencies=[Depends(require_admin)])


@router.get("/connectors")
async def get_connectors(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return await all_connector_reports(settings)
