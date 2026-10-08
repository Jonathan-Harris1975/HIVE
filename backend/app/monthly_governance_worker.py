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

from app.core.config import get_settings
from app.services.monthly_governance_jobs import claim_job, complete_job, get_job
from app.services.monthly_review import _period_bounds, generate_and_archive_monthly_review
from app.storage.d1 import D1MetadataStore

logger = logging.getLogger(__name__)


async def execute(period: str, *, owner: str | None = None) -> int:
    canonical, _, _ = _period_bounds(period)
    settings = get_settings()
    store = D1MetadataStore(settings)
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run monthly governance outside the HTTP gateway")
    parser.add_argument("--period", required=True, help="Reporting month YYYY-MM")
    parser.add_argument("--owner", help="Unique worker invocation identifier")
    args = parser.parse_args()
    return asyncio.run(execute(args.period, owner=args.owner))


if __name__ == "__main__":
    raise SystemExit(main())
