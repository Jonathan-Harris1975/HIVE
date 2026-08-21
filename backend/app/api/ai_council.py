from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.ai_council import get_run_history, run_council
from app.services.model_registry import list_categories
from app.services.model_sync import ModelSyncError, sync_model_registry_downstream

router = APIRouter(tags=["ai-council"], dependencies=[Depends(require_admin)])


@router.post("/ai-council/run")
async def post_run_council(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    report = await run_council(settings)
    try:
        downstream_sync = await sync_model_registry_downstream(
            settings,
            source_run_id=report.run_id,
            registry=list_categories(),
        )
    except ModelSyncError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "AI Council completed but downstream model governance did not",
                "sourceRunId": report.run_id,
                "reason": str(exc),
            },
        ) from exc
    return {**report.public_payload(), "downstream_sync": downstream_sync}


@router.get("/ai-council/history")
async def get_council_history(
    limit: int = Query(20, ge=1, le=200), settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return {"runs": get_run_history(settings, limit=limit)}
