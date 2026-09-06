"""Idempotent execution of the monthly AI Council governance cycle."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.services.ai_council import get_run_history, record_run_completion, run_council
from app.services.model_registry import list_categories
from app.services.model_sync import ModelSyncError, sync_model_registry_downstream


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)




def _qualified_registry(settings: Settings) -> tuple[dict[str, list[dict[str, object]]], int]:
    registry = list_categories()
    qualified_count = 0
    for items in registry.values():
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                score = float(item.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score >= settings.model_registry_min_visible_score:
                qualified_count += 1
    return registry, qualified_count

def latest_verified_run(settings: Settings, *, since: datetime | None = None) -> dict[str, Any] | None:
    """Return the newest fully-synchronised Council run, optionally bounded by freshness."""
    runs = get_run_history(settings, limit=50)
    for run in reversed(runs):
        if not isinstance(run, dict):
            continue
        sync = run.get("downstream_sync")
        if run.get("completion_status") != "completed" or not isinstance(sync, dict) or sync.get("ok") is not True:
            continue
        occurred = _parse_timestamp(run.get("completed_at") or run.get("occurred_at"))
        if since is not None and (occurred is None or occurred < since.astimezone(UTC)):
            continue
        return run
    return None


async def execute_council_cycle(
    settings: Settings,
    *,
    reuse_since: datetime | None = None,
) -> dict[str, Any]:
    """Run Council + AIMS/RAMS propagation, or reuse a verified fresh run.

    This is the single orchestration primitive used by both the on-demand
    Council endpoint and Monthly Review.  Monthly Review supplies ``reuse_since``
    so repeated calls in one calendar month do not run the Council twice.
    """
    if reuse_since is not None:
        existing = latest_verified_run(settings, since=reuse_since)
        if existing is not None:
            return {
                "ok": True,
                "reused": True,
                "run": existing,
                "completion_status": "completed",
                "downstream_sync": existing.get("downstream_sync"),
            }

    report = await run_council(settings)
    registry, qualified_count = _qualified_registry(settings)
    if qualified_count == 0:
        error = (
            "AI Council completed but the Model Registry has no qualified models; "
            "downstream governance sync was skipped"
        )
        downstream_sync = {
            "ok": False,
            "enabled": bool(settings.model_governance_sync_enabled),
            "skipped": True,
            "sourceRunId": report.run_id,
            "error": error,
        }
        record_run_completion(
            settings,
            run_id=report.run_id,
            completion_status="degraded",
            downstream_sync=downstream_sync,
        )
        return {
            "ok": False,
            "reused": False,
            "run": report.public_payload(),
            "completion_status": "degraded",
            "downstream_sync": downstream_sync,
            "failure_stage": "model_registry",
            "qualified_model_count": 0,
            "error": error,
        }

    try:
        downstream_sync = await sync_model_registry_downstream(
            settings,
            source_run_id=report.run_id,
            registry=registry,
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
        return {
            "ok": False,
            "reused": False,
            "run": report.public_payload(),
            "completion_status": "degraded",
            "downstream_sync": downstream_sync,
            "failure_stage": "downstream_sync",
            "qualified_model_count": qualified_count,
            "error": str(exc),
        }

    record_run_completion(
        settings,
        run_id=report.run_id,
        completion_status="completed",
        downstream_sync=downstream_sync,
    )
    return {
        "ok": True,
        "reused": False,
        "run": report.public_payload(),
        "completion_status": "completed",
        "downstream_sync": downstream_sync,
        "qualified_model_count": qualified_count,
    }
