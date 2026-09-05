from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.ai_council import get_run_history, record_run_completion, run_council
from app.services.model_registry import list_categories
from app.services.model_sync import ModelSyncError, sync_model_registry_downstream

router = APIRouter(tags=["ai-council"], dependencies=[Depends(require_admin)])
logger = logging.getLogger("uvicorn.error.hive.ai_council")


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
        downstream_sync = {
            "ok": False,
            "enabled": bool(settings.model_governance_sync_enabled),
            "sourceRunId": report.run_id,
            "error": str(exc),
        }
        record_run_completion(
            settings,
            run_id=report.run_id,
            completion_status="degraded",
            downstream_sync=downstream_sync,
        )
        logger.error(
            "AI Council downstream model-governance sync failed run_id=%s reason=%s",
            report.run_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "AI Council completed but downstream model governance did not",
                "sourceRunId": report.run_id,
                "reason": str(exc),
                "council_completed": True,
                "downstream_sync": downstream_sync,
            },
        ) from exc

    record_run_completion(
        settings,
        run_id=report.run_id,
        completion_status="completed",
        downstream_sync=downstream_sync,
    )
    return {
        **report.public_payload(),
        "ok": True,
        "completion_status": "completed",
        "downstream_sync": downstream_sync,
    }


@router.get("/ai-council/history")
async def get_council_history(
    limit: int = Query(20, ge=1, le=200), settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return {"runs": get_run_history(settings, limit=limit)}
