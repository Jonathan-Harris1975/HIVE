from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.workflow_presets import workflow_presets_payload

router = APIRouter(tags=["workflow-presets"], dependencies=[Depends(require_admin)])


@router.get("/workflow-presets")
def workflow_presets(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return HIVE's safe preset modes for file/chat workflows."""

    return workflow_presets_payload(settings)
