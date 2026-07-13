from __future__ import annotations

import json as _json

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.monthly_review import generate_and_archive_monthly_review, list_monthly_reviews
from app.storage.r2 import R2Storage

router = APIRouter(tags=["monthly-review"], dependencies=[Depends(require_admin)])


@router.post("/monthly-review/generate")
async def generate_monthly_review_endpoint(
    period: str | None = Query(None, description="Target month as 'YYYY-MM'. Defaults to the month that just finished."),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Generate, archive (R2) and index (D1) a Monthly Review report.

    Intended to be called by MAST's hive-governance-monthly job group after
    the individual data-gathering jobs (AI Council run, skills checks,
    optimisation stats snapshot) have already fired for the month.
    """
    try:
        report = await generate_and_archive_monthly_review(settings, period=period)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return report


@router.get("/monthly-review/history")
def list_monthly_review_history(
    limit: int = Query(24, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """List previously generated Monthly Review report summaries (most recent first)."""

    return list_monthly_reviews(settings, limit=limit)


@router.get("/monthly-review/{period}")
def get_monthly_review_by_period(
    period: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Fetch the full archived report body for a given 'YYYY-MM' period.

    Looks up the most recent report indexed for that period in D1, then
    reads the full JSON body back from R2.
    """
    history = list_monthly_reviews(settings, limit=200)
    if not history.get("ok"):
        return history

    matches = [item for item in history.get("items", []) if item.get("source_id") == period]
    if not matches:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"No monthly review found for period {period!r}.")

    # items are already ordered most-recent-first by list_metadata()
    latest = matches[0]
    r2_object = (latest.get("metadata") or {}).get("r2_object") or {}
    key = r2_object.get("key")
    bucket = r2_object.get("bucket")
    if not key or not bucket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Monthly review for {period!r} is indexed but its R2 archive location is missing.",
        )

    r2 = R2Storage(settings)
    raw = r2.read_object(key, max_bytes=8 * 1024 * 1024, bucket=bucket, read_only=True)

    try:
        return _json.loads(raw.content)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Archived monthly review for {period!r} could not be parsed: {exc}",
        ) from exc
