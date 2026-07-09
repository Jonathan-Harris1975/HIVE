from __future__ import annotations

# RC1 Remediation — Audit Finding #2 (Bucket Manager dual source of truth).
#
# The previous /v1/buckets endpoint served a static hardcoded list from
# bucket_manager.py that was disconnected from the live R2 lane registry used
# everywhere else in HIVE (files.py, config.py).  This created a dual source
# of truth that could silently diverge.
#
# This endpoint is now derived from the live `r2_ecosystem_lanes` property on
# Settings (the same authoritative source used by /v1/files/r2-lanes).
# bucket_manager.py's static lists are retained for backwards-compatible
# is_accessible() / assert_accessible() guards but are no longer the source
# of truth for the operator-facing bucket catalogue.

from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.bucket_manager import HIDDEN_BUCKETS

router = APIRouter(tags=["buckets"], dependencies=[Depends(require_admin)])


@router.get("/buckets")
async def get_buckets(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    """Return the live R2 bucket catalogue derived from the lane registry.

    This replaces the previous static list.  Buckets are sourced from the
    same `r2_ecosystem_lanes` property used by /v1/files/r2-lanes, so the
    two endpoints are always consistent.  Hidden internal buckets (e.g.
    ``metasystem``) are excluded.
    """
    lanes = settings.r2_ecosystem_lanes  # already excludes hidden lanes
    buckets = []
    seen: set[str] = set()
    for lane in lanes:
        bucket = lane.get("bucket")
        if not bucket or bucket in seen or bucket in HIDDEN_BUCKETS:
            continue
        seen.add(str(bucket))
        buckets.append(
            {
                "bucket": bucket,
                "lane": lane.get("lane"),
                "configured": lane.get("configured", False),
                "readable": lane.get("readable", False),
                "writable": lane.get("writable", False),
                "access_mode": lane.get("access_mode", "unknown"),
            }
        )

    return {
        "ok": True,
        "count": len(buckets),
        "buckets": buckets,
        "source": "live_lane_registry",
        "note": (
            "Bucket list is derived from the live R2 lane registry. "
            "Use /v1/files/r2-lanes for full lane details including public URLs."
        ),
    }
