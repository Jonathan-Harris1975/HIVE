from __future__ import annotations

import json
import logging
import time
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.execution_reviews import list_execution_review_plans
from app.services.optimisation_engine import list_decisions, list_experiments, success_rate_report
from app.services.repo_health import build_repo_health_report
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage
from app.storage.sql_store import SqlStore

# D1 ecosystem-metadata lane used to index generated reports (mirrors the
# pattern used by app.services.execution_reviews.EXECUTION_REVIEW_LANE).
MONTHLY_REVIEW_LANE = "hive_monthly_reviews"

# Cap how many past reports we keep indexed/listed by default.
logger = logging.getLogger(__name__)
DEFAULT_HISTORY_LIMIT = 24

def _period_bounds(period: str | None) -> tuple[str, str, str]:
    """Resolve a 'YYYY-MM' period (or the just-completed month if omitted)
    into (period_label, since_iso, until_iso) where `until` is exclusive.
    """
    now = datetime.now(timezone.utc)
    if period:
        try:
            year_str, month_str = period.split("-", 1)
            year, month = int(year_str), int(month_str)
            if not 1 <= month <= 12:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid period {period!r}; expected 'YYYY-MM'.") from exc
    else:
        # This is designed to run on day 1 of the month (see MAST's
        # hive-governance-monthly job group), reporting on the month that
        # just finished.
        first_of_this_month = now.replace(day=1)
        if first_of_this_month.month == 1:
            year, month = first_of_this_month.year - 1, 12
        else:
            year, month = first_of_this_month.year, first_of_this_month.month - 1

    since = datetime(year, month, 1, tzinfo=timezone.utc)
    until = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if month == 12
        else datetime(year, month + 1, 1, tzinfo=timezone.utc)
    )
    return f"{year:04d}-{month:02d}", since.isoformat(), until.isoformat()

def _normalise_section_result(data: Any) -> dict[str, Any]:
    """Wrap a section result without converting an explicit ``ok=False`` into success."""
    if isinstance(data, dict) and data.get("ok") is False:
        error = str(data.get("error") or data.get("reason") or "subsystem reported ok=false")
        return {"ok": False, "data": data, "error": error}
    return {"ok": True, "data": data}

def _section(fn, *args, **kwargs) -> dict[str, Any]:
    """Run one report section defensively. A failure in any single subsystem
    (e.g. D1 unreachable or a downstream probe failing) must not blank the rest of the
    monthly review -- it should show up as a flagged section instead."""
    try:
        return _normalise_section_result(fn(*args, **kwargs))
    except Exception as exc:  # noqa: BLE001 - deliberately broad: isolate section failures
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}

async def _async_section(coro) -> dict[str, Any]:
    try:
        return _normalise_section_result(await coro)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}








async def generate_monthly_review(
    settings: Settings,
    *,
    period: str | None = None,
) -> dict[str, Any]:
    """Aggregate system metrics, AI model performance, token usage/costs, repo
    health, advisory optimisation outcomes and execution posture for one calendar
    month into a single report. Individual subsystem failures are captured
    per-section rather than failing the whole report.
    """
    period_label, since, until = _period_bounds(period)
    generated_at = datetime.now(timezone.utc).isoformat()

    sections: dict[str, Any] = {
        "cost_and_tokens": _section(
            lambda: SqlStore(settings).cost_summary(by_model_limit=20, since=since, until=until)
        ),
        "model_governance": _section(
            lambda: SqlStore(settings).model_governance_audit(since=since, until=until)
        ),
        "optimisation_success_rate": _section(success_rate_report, settings),
        "optimisation_experiments": _section(list_experiments, settings),
        "optimisation_decisions": _section(list_decisions, settings),
        "execution_reviews": _section(
            list_execution_review_plans, settings=settings, status=None, repo=None, limit=500
        ),
    }
    sections["repo_health"] = await _async_section(build_repo_health_report(settings))

    ok_sections = sum(1 for value in sections.values() if value.get("ok"))
    report: dict[str, Any] = {
        "ok": ok_sections == len(sections),
        "report_id": f"monthly-review-{period_label}-{uuid.uuid4().hex[:8]}",
        "period": period_label,
        "period_since": since,
        "period_until": until,
        "generated_at": generated_at,
        "sections_ok": ok_sections,
        "sections_total": len(sections),
        "sections": sections,
    }
    return report

def _write_report_to_r2(settings: Settings, report: dict[str, Any]) -> dict[str, Any] | None:
    """Archive the full report JSON to R2 and return a machine-checkable
    result descriptor. Failures are captured in the descriptor so scheduler
    response policy can fail closed without losing the diagnostic payload."""
    try:
        r2 = R2Storage(settings)
        key = f"monthly-reviews/{report['period']}/{report['report_id']}.json"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "report.json"
            tmp_path.write_text(
                json.dumps(report, ensure_ascii=False, default=str, indent=2), encoding="utf-8"
            )
            stored = r2.put_file(
                tmp_path,
                key,
                content_type="application/json",
                bucket=settings.r2_bucket_audits,
                public_base_url=None,
            )
        return {
            "ok": True,
            "bucket": stored.bucket,
            "key": stored.key,
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

def _index_report_in_d1(
    settings: Settings, report: dict[str, Any], r2_object: dict[str, Any] | None
) -> dict[str, Any]:
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False}
    summary = {
        "period": report["period"],
        "generated_at": report["generated_at"],
        "sections_ok": report["sections_ok"],
        "sections_total": report["sections_total"],
        "ok": bool(report.get("ok") and r2_object and r2_object.get("ok")),
        "r2_object": r2_object,
        "cost_usd_total": (
            report["sections"]
            .get("cost_and_tokens", {})
            .get("data", {})
            .get("totals", {})
            .get("cost_usd")
        ),
        "open_execution_reviews": (
            report["sections"].get("execution_reviews", {}).get("data", {}).get("open_count")
        ),
    }
    return d1.upsert_metadata(
        item_id=report["report_id"],
        lane=MONTHLY_REVIEW_LANE,
        source_type="monthly_review",
        source_id=report["period"],
        title=f"Monthly Review {report['period']}",
        url=None,
        metadata=summary,
    )

async def generate_and_archive_monthly_review(
    settings: Settings, *, period: str | None = None
) -> dict[str, Any]:
    """Build, archive and index the monthly operations/cost report."""
    _period_bounds(period)
    report_started = time.monotonic()
    logger.info("monthly_review stage started", extra={"stage": "report"})
    try:
        report = await generate_monthly_review(
            settings,
            period=period,
        )
    finally:
        logger.info("monthly_review stage elapsed", extra={"stage": "report", "elapsed_seconds": time.monotonic() - report_started})

    archive_started = time.monotonic()
    logger.info("monthly_review stage started", extra={"stage": "r2"})
    try:
        r2_object = _write_report_to_r2(settings, report)
    finally:
        logger.info("monthly_review stage elapsed", extra={"stage": "r2", "elapsed_seconds": time.monotonic() - archive_started})
    index_started = time.monotonic()
    logger.info("monthly_review stage started", extra={"stage": "d1"})
    try:
        index_result = _index_report_in_d1(settings, report, r2_object)
    finally:
        logger.info("monthly_review stage elapsed", extra={"stage": "d1", "elapsed_seconds": time.monotonic() - index_started})
    report["r2_object"] = r2_object
    report["d1_index"] = index_result
    report["ok"] = bool(
        report.get("ok") and r2_object and r2_object.get("ok") and index_result.get("ok")
    )
    return report

def list_monthly_reviews(
    settings: Settings, *, limit: int = DEFAULT_HISTORY_LIMIT
) -> dict[str, Any]:
    """List previously generated reports (summary only; fetch the R2 object
    for the full report body)."""
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False}
    result = d1.list_metadata(
        lane=MONTHLY_REVIEW_LANE, limit=max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), 200))
    )
    return result
