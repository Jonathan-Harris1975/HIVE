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
    qualified: dict[str, list[dict[str, object]]] = {}
    qualified_count = 0
    for category, items in registry.items():
        eligible: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_score = item.get("score")
            try:
                score = float(raw_score) if isinstance(raw_score, (int, float, str)) else 0.0
            except ValueError:
                score = 0.0
            lifecycle = str(item.get("lifecycle_status") or "active")
            if score >= settings.model_registry_min_visible_score and lifecycle in {
                "active",
                "watch",
            }:
                qualified_count += 1
                eligible.append(item)
        qualified[category] = eligible
    return qualified, qualified_count


def latest_verified_run(
    settings: Settings, *, since: datetime | None = None
) -> dict[str, Any] | None:
    """Return the latest run only if verified; later failures supersede earlier success."""
    runs = get_run_history(settings, limit=50)
    if not runs or not isinstance(runs[-1], dict):
        return None
    latest = runs[-1]
    sync = latest.get("downstream_sync")
    if (
        latest.get("completion_status") != "completed"
        or not isinstance(sync, dict)
        or sync.get("enabled") is not True
        or sync.get("ok") is not True
    ):
        return None
    occurred = _parse_timestamp(latest.get("completed_at") or latest.get("occurred_at"))
    if since is not None and (occurred is None or occurred < since.astimezone(UTC)):
        return None
    return latest


def monthly_governance_status(
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return freshness of the newest Council run for the current month.

    A later degraded run must supersede an earlier success.  Production model
    governance is only healthy when the newest current-month run completed and
    actually propagated to both downstream services.
    """

    current = (now or datetime.now(UTC)).astimezone(UTC)
    required_since = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    runs = get_run_history(settings, limit=50)
    latest = runs[-1] if runs and isinstance(runs[-1], dict) else {}
    latest_completed = _parse_timestamp(latest.get("completed_at") or latest.get("occurred_at"))
    latest_sync = latest.get("downstream_sync") if isinstance(latest, dict) else None
    completion_status = str(latest.get("completion_status") or "missing") if latest else "missing"
    sync_enabled = bool(isinstance(latest_sync, dict) and latest_sync.get("enabled") is True)
    downstream_sync_ok = bool(
        isinstance(latest_sync, dict)
        and latest_sync.get("ok") is True
        and sync_enabled
    )
    fresh = bool(latest_completed and latest_completed >= required_since)
    verified = bool(
        latest
        and fresh
        and completion_status == "completed"
        and downstream_sync_ok
    )

    if verified:
        return {
            "ok": True,
            "fresh": True,
            "period": required_since.strftime("%Y-%m"),
            "required_since": required_since.isoformat(),
            "latest_verified_run_id": latest.get("run_id"),
            "latest_verified_at": latest_completed.isoformat() if latest_completed else None,
            "completion_status": completion_status,
            "downstream_sync_enabled": True,
            "downstream_sync_ok": True,
            "reason": None,
        }

    if not runs:
        reason = "no AI Council run history available"
    elif not fresh:
        reason = "no verified AI Council run exists for the current monthly governance period"
    elif completion_status != "completed":
        reason = f"latest AI Council run is {completion_status}"
    elif not sync_enabled:
        reason = "latest AI Council run did not enable downstream AIMS/RAMS model propagation"
    elif not downstream_sync_ok:
        reason = "latest AI Council run did not verify downstream AIMS/RAMS model propagation"
    else:
        reason = "latest AI Council run is not verified complete"

    return {
        "ok": False,
        "fresh": fresh,
        "period": required_since.strftime("%Y-%m"),
        "required_since": required_since.isoformat(),
        "latest_verified_run_id": None,
        "latest_verified_at": None,
        "latest_run_id": latest.get("run_id") if latest else None,
        "latest_run_at": latest_completed.isoformat() if latest_completed else None,
        "completion_status": completion_status,
        "downstream_sync_enabled": sync_enabled,
        "downstream_sync_ok": downstream_sync_ok,
        "reason": reason,
    }


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

    if downstream_sync.get("enabled") is not True or downstream_sync.get("ok") is not True:
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
            "error": "Downstream model synchronisation is disabled or unverified",
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
