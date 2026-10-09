"""Independent monthly governance worker entrypoint.

Run from a persistent process or scheduled container with HIVE settings.
Never automatically reclaim ambiguous claimed jobs: downstream writes may
already have happened before the worker stopped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.services.monthly_governance_jobs import claim_job, complete_job, get_job
from app.services.monthly_review import _period_bounds, generate_and_archive_monthly_review
from app.storage.d1 import D1MetadataStore

logger = logging.getLogger(__name__)


def check_readiness(settings) -> list[str]:
    """Validate required worker configuration without printing secret values."""
    missing = []
    if not D1MetadataStore(settings).enabled:
        missing.append("D1_ENABLED, D1_ACCOUNT_ID, D1_DATABASE_ID and D1_API_KEY")
    for attribute, label in (
        ("cf_r2_account_id", "CF_R2_ACCOUNT_ID"),
        ("cf_r2_access_key_id", "CF_R2_ACCESS_KEY_ID"),
        ("cf_r2_secret_access_key", "CF_R2_SECRET_ACCESS_KEY"),
        ("r2_bucket_audits", "R2_BUCKET_AUDITS"),
        ("aims_base_url", "AIMS_BASE_URL"),
        ("aims_api_key", "AIMS_API_KEY"),
        ("rams_base_url", "RAMS_BASE_URL"),
        ("rams_api_key", "RAMS_API_KEY"),
    ):
        if not str(getattr(settings, attribute, "") or "").strip():
            missing.append(label)
    return missing


async def execute(period: str, *, owner: str | None = None, preflight_only: bool = False) -> int:
    canonical, _, _ = _period_bounds(period)
    if canonical >= datetime.now(UTC).strftime("%Y-%m"):
        logger.error("Monthly governance period must be a completed UTC month")
        return 7
    settings = get_settings()
    missing = check_readiness(settings)
    if missing:
        logger.error("Monthly governance worker missing configuration: %s", ", ".join(missing))
        return 5
    store = D1MetadataStore(settings)
    if preflight_only:
        probe = await asyncio.to_thread(store.diagnostics)
        if not probe.get("ok") or not probe.get("schema_ready"):
            logger.error("Monthly governance D1 schema/connectivity preflight failed")
            return 6
        logger.info("Monthly governance worker preflight passed")
        return 0
    worker = owner or uuid.uuid4().hex
    claim = await asyncio.to_thread(claim_job, store, period=canonical, owner=worker)
    if not claim.get("ok"):
        logger.error("Monthly governance job claim failed: %s", claim.get("error"))
        return 2
    if not claim.get("claimed"):
        existing = await asyncio.to_thread(get_job, store, period=canonical)
        logger.warning("Monthly governance job already exists: %s", existing)
        return 3
    try:
        report = await generate_and_archive_monthly_review(settings, period=canonical)
    except BaseException:
        # An interrupted or crashed worker may have committed partial writes.
        # Preserve the claim so operators can investigate before any retry.
        logger.exception("Monthly governance worker interrupted; claim retained")
        raise
    finished = await asyncio.to_thread(
        complete_job, store, period=canonical, owner=worker,
        succeeded=report.get("ok") is True,
    )
    if not finished.get("ok") or not finished.get("completed"):
        logger.error("Unable to persist terminal governance status: %s", finished)
        return 4
    print(json.dumps({"period": canonical, "job": claim["job_id"],
                      "ok": report.get("ok"), "report_id": report.get("report_id"),
                      "sections_ok": report.get("sections_ok"),
                      "sections_total": report.get("sections_total")}))
    return 0 if report.get("ok") is True else 1


def previous_completed_utc_month(now: datetime | None = None) -> str:
    """Resolve the prior completed UTC month, including January rollover."""
    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    return (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run monthly governance outside the HTTP gateway")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--period", help="Reporting month YYYY-MM")
    selection.add_argument("--previous-month", action="store_true", help="Use the previous completed UTC month")
    parser.add_argument("--owner", help="Unique worker invocation identifier")
    parser.add_argument("--preflight-only", action="store_true", help="Check settings and D1 without governance writes")
    args = parser.parse_args()
    period = previous_completed_utc_month() if args.previous_month else args.period
    return asyncio.run(execute(period, owner=args.owner, preflight_only=args.preflight_only))


if __name__ == "__main__":
    raise SystemExit(main())
