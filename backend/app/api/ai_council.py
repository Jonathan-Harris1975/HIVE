from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.ai_council import get_run_history
from app.services.council_cycle import execute_council_cycle

router = APIRouter(tags=["ai-council"], dependencies=[Depends(require_admin)])


@router.post("/ai-council/run")
async def post_run_council(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    cycle = await execute_council_cycle(settings)
    run = cycle.get("run") if isinstance(cycle.get("run"), dict) else {}
    if not cycle.get("ok"):
        registry_failure = cycle.get("failure_stage") == "model_registry"
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if registry_failure
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail={
                "error": (
                    "AI Council completed but no model qualified for the Model Registry"
                    if registry_failure
                    else "AI Council completed but downstream model governance did not"
                ),
                "sourceRunId": run.get("run_id"),
                "reason": cycle.get("error") or "monthly AI Council governance failed",
                "council_completed": True,
                "failure_stage": cycle.get("failure_stage"),
                "qualified_model_count": cycle.get("qualified_model_count"),
                "downstream_sync": cycle.get("downstream_sync"),
            },
        )
    return {
        **run,
        "ok": True,
        "completion_status": cycle.get("completion_status"),
        "downstream_sync": cycle.get("downstream_sync"),
        "reused": bool(cycle.get("reused")),
    }


@router.get("/ai-council/history")
async def get_council_history(
    limit: int = Query(20, ge=1, le=200), settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    return {"runs": get_run_history(settings, limit=limit)}
