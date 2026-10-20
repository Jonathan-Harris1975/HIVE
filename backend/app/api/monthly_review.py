from __future__ import annotations

import asyncio
import json as _json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.monthly_governance_jobs import claim_job, complete_job, get_job
from app.services.monthly_review import _period_bounds, generate_and_archive_monthly_review, list_monthly_reviews
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage

router = APIRouter(tags=["monthly-review"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


async def _run_monthly_review_job(settings: Settings, period: str, owner: str) -> None:
    """Run the long monthly operation after the request has returned 202.

    A crashed or interrupted task deliberately leaves its D1 claim in place:
    operators must reconcile possible downstream writes before any retry.
    """
    try:
        report = await generate_and_archive_monthly_review(settings, period=period)
    except Exception:
        logger.exception("Monthly governance job interrupted; D1 claim retained period=%s", period)
        raise

    finished = await asyncio.to_thread(
        complete_job,
        D1MetadataStore(settings),
        period=period,
        owner=owner,
        succeeded=report.get("ok") is True,
    )
    if not finished.get("ok") or not finished.get("completed"):
        logger.error("Monthly governance terminal status was not persisted period=%s", period)




@router.post("/monthly-review/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_monthly_review_endpoint(
    background_tasks: BackgroundTasks,
    period: str | None = Query(None, description="Target month as 'YYYY-MM'. Defaults to the month that just finished."),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Atomically accept a monthly review and return before the long work begins.

    MAST owns the schedule. It polls the persistent D1 job status until terminal
    completion. The D1 claim fences duplicate writes when a request is retried or
    the service is restarted.
    """
    try:
        canonical, _, _ = _period_bounds(period)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if canonical >= datetime.now(UTC).strftime("%Y-%m"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Monthly governance can only run for a completed UTC month.",
        )

    owner = f"api-{uuid.uuid4().hex}"
    store = D1MetadataStore(settings)
    claim = await asyncio.to_thread(claim_job, store, period=canonical, owner=owner)
    if not claim.get("ok"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=claim)
    if not claim.get("claimed"):
        existing = await asyncio.to_thread(get_job, store, period=canonical)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "Monthly governance job already exists; reconcile its status before retrying.", "job": existing},
        )

    background_tasks.add_task(_run_monthly_review_job, settings, canonical, owner)
    return {
        "ok": True,
        "accepted": True,
        "period": canonical,
        "job_id": claim["job_id"],
        "status": "claimed",
        "status_url": f"/v1/monthly-review/jobs/{canonical}",
    }


@router.get("/monthly-review/jobs/{period}")
def monthly_review_job_status(
    period: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Read persistent worker status; never starts or repeats governance writes."""
    try:
        canonical, _, _ = _period_bounds(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = get_job(D1MetadataStore(settings), period=canonical)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    if not result.get("found"):
        raise HTTPException(status_code=404, detail="Monthly governance job not found")
    return result


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
