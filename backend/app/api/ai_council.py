from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.ai_council import get_run_history, run_council

router = APIRouter(tags=["ai-council"], dependencies=[Depends(require_admin)])


@router.post("/ai-council/run")
async def post_run_council(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    report = await run_council(settings)
    return report.public_payload()


@router.get("/ai-council/history")
async def get_council_history(
    limit: int = Query(20, ge=1, le=200), settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return {"runs": get_run_history(settings, limit=limit)}
