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


# ---------------------------------------------------------------------------
# RAMS QA-event ingestion adapter.
#
# This is the wiring the deployment-readiness audit flagged as critical:
# previously nothing connected an incoming RAMS QA event to the
# optimisation decision ledger above. A QA event describes something RAMS
# observed about a repository/skill/workflow it QA'd (a score, a pass/fail
# check, a recommended action); this adapter turns that observation into a
# recorded, reviewable, and rollback-able optimisation decision so it shows
# up in both `list_decisions` and `success_rate_report` (the two places the
# audit's integration test checks).
# ---------------------------------------------------------------------------

REQUIRED_QA_EVENT_FIELDS = ("event_id", "category", "subject_id", "qa_score", "recommendation")


class QAEventValidationError(ValueError):
    pass


def _validate_qa_event(payload: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_QA_EVENT_FIELDS if field not in payload]
    if missing:
        raise QAEventValidationError(f"QA event missing required field(s): {', '.join(missing)}")
    qa_score = payload["qa_score"]
    if not isinstance(qa_score, (int, float)) or not (0.0 <= float(qa_score) <= 1.0):
        raise QAEventValidationError("QA event 'qa_score' must be a number between 0.0 and 1.0")


def ingest_qa_event(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    """Ingest a single QA event from RAMS and record it as an optimisation
    decision. Idempotent on `event_id`: re-ingesting the same event_id
    returns the existing decision rather than creating a duplicate, since
    RAMS may retry delivery.

    Returns the recorded decision dict (same shape `record_decision` and
    `get_decision` return), plus '_ingested' indicating whether this call
    created a new decision (True) or matched an existing one (False).
    """
    _validate_qa_event(payload)

    event_id = str(payload["event_id"])
    existing = _find_decision_by_source_event(settings, event_id)
    if existing is not None:
        return {**existing, "_ingested": False}

    qa_score = float(payload["qa_score"])
    decision = record_decision(
        settings,
        decision_type="rams_qa_event",
        description=(
            f"RAMS QA event for {payload['subject_id']} "
            f"(category={payload['category']}): {payload['recommendation']}"
        ),
        previous_state={"source": "rams", "event_id": event_id, "raw": payload},
        new_state={"recommendation": payload["recommendation"], "applied": True},
        confidence=qa_score,
    )
    decision["source_event_id"] = event_id
    decision["source"] = "rams_qa_event"
    # Persist the enriched fields (record_decision already wrote the base
    # record; overwrite with the source-tracking fields added above so
    # idempotency lookups and audit trails have them).
    store = D1MetadataStore(settings)
    store.upsert_metadata(
        item_id=f"optimisation:decision:{decision['decision_id']}",
        lane=LANE,
        source_type="decision",
        source_id=decision["decision_id"],
        title=decision["description"],
        url=None,
        metadata=decision,
    )
    return {**decision, "_ingested": True}


def _find_decision_by_source_event(settings: Settings, event_id: str) -> dict[str, Any] | None:
    for decision in list_decisions(settings, decision_type="rams_qa_event"):
        if decision.get("source_event_id") == event_id:
            return decision
    return None
