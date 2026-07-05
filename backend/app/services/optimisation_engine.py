from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.storage.d1 import D1MetadataStore

# Phase 11 - Optimisation Engine.
#
# Tracks every optimisation decision HIVE makes (e.g. an AI Council
# promotion, a Repository Council recommendation acted upon) so it can be
# reviewed and, if it turns out to be wrong, rolled back. "Reversible" here
# means the engine always records enough state (`previous_state`) to know
# what to revert *to*; actually re-applying that state to whatever real
# system the decision touched is the caller's responsibility — this engine
# is the ledger, not the actuator.

LANE = "optimisation_engine"


class OptimisationEngineError(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def record_decision(
    settings: Settings,
    *,
    decision_type: str,
    description: str,
    previous_state: Any,
    new_state: Any,
    confidence: float,
) -> dict[str, Any]:
    store = D1MetadataStore(settings)
    decision_id = uuid.uuid4().hex
    record = {
        "decision_id": decision_id,
        "decision_type": decision_type,
        "description": description,
        "previous_state": previous_state,
        "new_state": new_state,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "status": "applied",
        "created_at": _now_iso(),
        "reverted_at": None,
    }
    store.upsert_metadata(
        item_id=f"optimisation:decision:{decision_id}",
        lane=LANE,
        source_type="decision",
        source_id=decision_id,
        title=description,
        url=None,
        metadata=record,
    )
    return record


def rollback_decision(settings: Settings, decision_id: str) -> dict[str, Any]:
    store = D1MetadataStore(settings)
    decision = get_decision(settings, decision_id)
    if decision is None:
        raise OptimisationEngineError(f"Unknown decision_id: {decision_id}")
    if decision["status"] == "reverted":
        return decision

    decision["status"] = "reverted"
    decision["reverted_at"] = _now_iso()
    store.upsert_metadata(
        item_id=f"optimisation:decision:{decision_id}",
        lane=LANE,
        source_type="decision",
        source_id=decision_id,
        title=decision["description"],
        url=None,
        metadata=decision,
    )
    return decision


def get_decision(settings: Settings, decision_id: str) -> dict[str, Any] | None:
    store = D1MetadataStore(settings)
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return None
    for row in result.get("items", []):
        if row.get("source_type") == "decision" and row.get("source_id") == decision_id:
            return row.get("metadata") or {}
    return None


def list_decisions(settings: Settings, *, decision_type: str | None = None) -> list[dict[str, Any]]:
    store = D1MetadataStore(settings)
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return []
    decisions = [
        row.get("metadata") or {}
        for row in result.get("items", [])
        if row.get("source_type") == "decision"
    ]
    if decision_type:
        decisions = [d for d in decisions if d.get("decision_type") == decision_type]
    return sorted(decisions, key=lambda d: d.get("created_at") or "", reverse=True)


def record_experiment(
    settings: Settings, *, name: str, hypothesis: str, outcome: str, success: bool
) -> dict[str, Any]:
    store = D1MetadataStore(settings)
    experiment_id = uuid.uuid4().hex
    record = {
        "experiment_id": experiment_id,
        "name": name,
        "hypothesis": hypothesis,
        "outcome": outcome,
        "success": success,
        "created_at": _now_iso(),
    }
    store.upsert_metadata(
        item_id=f"optimisation:experiment:{experiment_id}",
        lane=LANE,
        source_type="experiment",
        source_id=experiment_id,
        title=name,
        url=None,
        metadata=record,
    )
    return record


def list_experiments(settings: Settings) -> list[dict[str, Any]]:
    store = D1MetadataStore(settings)
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return []
    return [
        row.get("metadata") or {}
        for row in result.get("items", [])
        if row.get("source_type") == "experiment"
    ]


def success_rate_report(settings: Settings) -> dict[str, Any]:
    decisions = list_decisions(settings)
    experiments = list_experiments(settings)
    applied = [d for d in decisions if d.get("status") == "applied"]
    reverted = [d for d in decisions if d.get("status") == "reverted"]
    successful_experiments = [e for e in experiments if e.get("success")]

    return {
        "decision_count": len(decisions),
        "applied_count": len(applied),
        "reverted_count": len(reverted),
        "rollback_rate": (len(reverted) / len(decisions)) if decisions else 0.0,
        "experiment_count": len(experiments),
        "experiment_success_rate": (len(successful_experiments) / len(experiments)) if experiments else 0.0,
    }
