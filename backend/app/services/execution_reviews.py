from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.services.skill_registry import shared_execution_plan
from app.storage.d1 import D1MetadataStore

EXECUTION_REVIEW_LANE = "hive_execution_reviews"
SOURCE_TYPE = "execution_review_plan"
ALLOWED_DECISIONS = {"approved", "rejected", "needs_changes", "archived"}
OPEN_STATUSES = {"pending_review", "needs_changes"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_execution_review_plan(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    requested_by: str | None = None,
    limit: int = 5,
    dry_run: bool = True,
) -> dict[str, object]:
    """Create a reviewable execution plan record in D1.

    v1.15 is still intentionally plan/review only. This endpoint does not run
    tools, mutate repos, install skills, or create background jobs. It stores a
    plan so the future UI can show an approval queue.
    """

    clean_task = " ".join((task or "").strip().split())[:1200]
    if not clean_task:
        return {"ok": False, "error_code": "missing_task", "message": "task is required."}

    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}

    plan = shared_execution_plan(
        settings=settings,
        task=clean_task,
        repo=repo,
        workflow_preset=workflow_preset,
        limit=limit,
    )
    if not plan.get("ok"):
        return plan

    plan_id = f"exec-plan-{uuid4()}"
    created_at = _now()
    metadata: dict[str, Any] = {
        "plan_id": plan_id,
        "status": "pending_review",
        "task": clean_task,
        "repo": repo,
        "workflow_preset": workflow_preset,
        "requested_by": requested_by or "hive-user",
        "created_at": created_at,
        "updated_at": created_at,
        "execution_mode": "plan_only",
        "can_execute_now": False,
        "requires_approval": True,
        "review_gate": {
            "state": "pending_review",
            "approved": False,
            "approved_by": None,
            "approved_at": None,
        },
        "decision_log": [],
        "plan": plan,
    }

    title = _title_for_plan(clean_task, repo=repo, workflow_preset=workflow_preset)
    if dry_run:
        return {
            "ok": True,
            "enabled": True,
            "dry_run": True,
            "build_stage_hint": BUILD_STAGE,
            "lane": EXECUTION_REVIEW_LANE,
            "plan_id": plan_id,
            "status": "pending_review",
            "title": title,
            "review": metadata,
            "safety_note": _safety_note(),
        }

    result = d1.upsert_metadata(
        item_id=plan_id,
        lane=EXECUTION_REVIEW_LANE,
        source_type=SOURCE_TYPE,
        source_id=plan_id,
        title=title,
        url=None,
        metadata=metadata,
    )
    return {
        "ok": bool(result.get("ok")),
        "enabled": True,
        "dry_run": False,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "plan_id": plan_id,
        "status": "pending_review",
        "title": title,
        "d1_result": result,
        "review": metadata,
        "safety_note": _safety_note(),
    }


def list_execution_review_plans(
    *,
    settings: Settings,
    status: str | None = None,
    repo: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}
    payload = d1.list_metadata(lane=EXECUTION_REVIEW_LANE, limit=max(1, min(int(limit or 50), 500)))
    if not payload.get("ok"):
        return payload
    reviews = [_review_summary(item) for item in payload.get("items", []) if isinstance(item, dict)]
    if status:
        clean_status = _clean_status(status)
        reviews = [item for item in reviews if item.get("status") == clean_status]
    if repo:
        clean_repo = repo.strip().lower()
        reviews = [item for item in reviews if str(item.get("repo") or "").lower() == clean_repo]
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "count": len(reviews),
        "open_count": sum(1 for item in reviews if item.get("status") in OPEN_STATUSES),
        "items": reviews,
        "filters": {"status": status, "repo": repo},
        "safety_note": _safety_note(),
    }


def get_execution_review_plan(*, settings: Settings, plan_id: str) -> dict[str, object]:
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}
    item = _get_review_item(d1, plan_id)
    if not item:
        return {"ok": False, "enabled": True, "error_code": "execution_plan_not_found", "plan_id": plan_id}
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "plan_id": plan_id,
        "review": item,
        "safety_note": _safety_note(),
    }


def decide_execution_review_plan(
    *,
    settings: Settings,
    plan_id: str,
    decision: str,
    reviewer: str | None = None,
    note: str | None = None,
) -> dict[str, object]:
    clean_decision = _clean_status(decision)
    if clean_decision not in ALLOWED_DECISIONS:
        return {
            "ok": False,
            "error_code": "invalid_decision",
            "allowed_decisions": sorted(ALLOWED_DECISIONS),
            "decision": decision,
        }

    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}

    item = _get_review_item(d1, plan_id)
    if not item:
        return {"ok": False, "enabled": True, "error_code": "execution_plan_not_found", "plan_id": plan_id}

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    now = _now()
    decision_entry = {
        "decision": clean_decision,
        "reviewer": reviewer or "hive-user",
        "note": (note or "").strip()[:1000] or None,
        "decided_at": now,
    }
    log = metadata.get("decision_log") if isinstance(metadata.get("decision_log"), list) else []
    log.append(decision_entry)
    metadata["decision_log"] = log
    metadata["status"] = clean_decision
    metadata["updated_at"] = now
    metadata["review_gate"] = {
        "state": clean_decision,
        "approved": clean_decision == "approved",
        "approved_by": decision_entry["reviewer"] if clean_decision == "approved" else None,
        "approved_at": now if clean_decision == "approved" else None,
    }
    metadata["can_execute_now"] = False
    metadata["requires_approval"] = clean_decision != "approved"

    result = d1.upsert_metadata(
        item_id=str(item.get("id") or plan_id),
        lane=EXECUTION_REVIEW_LANE,
        source_type=SOURCE_TYPE,
        source_id=plan_id,
        title=str(item.get("title") or _title_for_plan(str(metadata.get("task") or plan_id))),
        url=item.get("url"),
        metadata=metadata,
    )
    return {
        "ok": bool(result.get("ok")),
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "plan_id": plan_id,
        "decision": clean_decision,
        "d1_result": result,
        "review": metadata,
        "safety_note": _safety_note(),
    }



def execution_review_audit_trail(*, settings: Settings, plan_id: str) -> dict[str, object]:
    """Return a compact audit trail for one execution review plan.

    This is UI/export friendly and deliberately read-only.
    """

    detail = get_execution_review_plan(settings=settings, plan_id=plan_id)
    if not detail.get("ok"):
        return detail
    item = detail.get("review") if isinstance(detail.get("review"), dict) else {}
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    decision_log = meta.get("decision_log") if isinstance(meta.get("decision_log"), list) else []
    timeline = [
        {
            "event": "created",
            "at": meta.get("created_at") or item.get("created_at"),
            "actor": meta.get("requested_by") or "hive-user",
            "status": "pending_review",
            "note": "Execution review plan created.",
        }
    ]
    for entry in decision_log:
        if isinstance(entry, dict):
            timeline.append({
                "event": "decision",
                "at": entry.get("decided_at"),
                "actor": entry.get("reviewer") or "hive-user",
                "status": entry.get("decision"),
                "note": entry.get("note"),
            })
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "plan_id": plan_id,
        "status": meta.get("status") or "unknown",
        "timeline": timeline,
        "decision_count": len(decision_log),
        "safety_note": _safety_note(),
    }


def execution_review_evidence_pack(*, settings: Settings, plan_id: str) -> dict[str, object]:
    """Build a read-only evidence pack for one review plan.

    The pack is intended for the future UI and for copy/paste review outside HIVE.
    It contains the plan, candidate skills, decision trail and safety guardrails,
    but never executes an action.
    """

    detail = get_execution_review_plan(settings=settings, plan_id=plan_id)
    if not detail.get("ok"):
        return detail
    item = detail.get("review") if isinstance(detail.get("review"), dict) else {}
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    plan = meta.get("plan") if isinstance(meta.get("plan"), dict) else {}
    routed = plan.get("routed_skill_plan") if isinstance(plan.get("routed_skill_plan"), dict) else {}
    primary = routed.get("primary_skill") if isinstance(routed.get("primary_skill"), dict) else None
    candidates = routed.get("candidate_skills") if isinstance(routed.get("candidate_skills"), list) else []
    audit = execution_review_audit_trail(settings=settings, plan_id=plan_id)
    pack = {
        "plan_id": plan_id,
        "status": meta.get("status") or "unknown",
        "task": meta.get("task"),
        "repo": meta.get("repo"),
        "workflow_preset": meta.get("workflow_preset"),
        "requested_by": meta.get("requested_by"),
        "created_at": meta.get("created_at") or item.get("created_at"),
        "updated_at": meta.get("updated_at") or item.get("updated_at"),
        "execution_mode": "plan_only",
        "can_execute_now": False,
        "requires_approval": True,
        "primary_skill": primary,
        "candidate_skills": candidates,
        "candidate_count": len(candidates),
        "shared_steps": plan.get("shared_steps") or [],
        "guardrails": plan.get("guardrails") or {},
        "review_gate": meta.get("review_gate") or {},
        "decision_log": meta.get("decision_log") if isinstance(meta.get("decision_log"), list) else [],
        "audit_timeline": audit.get("timeline") if audit.get("ok") else [],
        "source_record": {
            "lane": item.get("lane"),
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "title": item.get("title"),
        },
    }
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "plan_id": plan_id,
        "evidence_pack": pack,
        "export_formats": ["json", "markdown"],
        "safety_note": _safety_note(),
    }


def export_execution_review_pack(*, settings: Settings, plan_id: str, export_format: str = "json") -> dict[str, object]:
    """Return an inline export document for a review evidence pack.

    Export is response-only in v1.15. It does not write to R2 or trigger a job.
    """

    pack_payload = execution_review_evidence_pack(settings=settings, plan_id=plan_id)
    if not pack_payload.get("ok"):
        return pack_payload
    pack = pack_payload.get("evidence_pack") if isinstance(pack_payload.get("evidence_pack"), dict) else {}
    fmt = (export_format or "json").strip().lower()
    if fmt not in {"json", "markdown", "md"}:
        return {"ok": False, "error_code": "unsupported_export_format", "allowed_formats": ["json", "markdown"], "format": export_format}
    if fmt in {"markdown", "md"}:
        content_type = "text/markdown; charset=utf-8"
        filename = f"{plan_id}-evidence-pack.md"
        content = _evidence_pack_markdown(pack)
        fmt = "markdown"
    else:
        content_type = "application/json; charset=utf-8"
        filename = f"{plan_id}-evidence-pack.json"
        content = json.dumps(pack, indent=2, ensure_ascii=False, default=str)
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_REVIEW_LANE,
        "plan_id": plan_id,
        "format": fmt,
        "filename": filename,
        "content_type": content_type,
        "content_chars": len(content),
        "export_document": content,
        "storage": "inline_response_only",
        "can_execute_now": False,
        "safety_note": _safety_note(),
    }

def _get_review_item(d1: D1MetadataStore, plan_id: str) -> dict[str, Any] | None:
    result = d1.query(
        """
        SELECT id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at
        FROM hive_ecosystem_metadata
        WHERE lane = ? AND id = ?
        LIMIT 1
        """,
        [EXECUTION_REVIEW_LANE, plan_id],
    )
    if not result.get("ok"):
        return None
    for row in _extract_rows(result.get("result")):
        row["metadata"] = _json_or_none(row.pop("metadata_json", None))
        return row
    return None


def _extract_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        rows: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                nested = item.get("results")
                if isinstance(nested, list):
                    rows.extend(row for row in nested if isinstance(row, dict))
                elif all(key in item for key in ("id", "lane", "source_type")):
                    rows.append(item)
        return rows
    if isinstance(result, dict):
        nested = result.get("results")
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
    return []


def _json_or_none(value: Any) -> Any:
    if value in {None, ""}:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _review_summary(item: dict[str, Any]) -> dict[str, object]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "id": item.get("id"),
        "plan_id": meta.get("plan_id") or item.get("id"),
        "status": meta.get("status") or "unknown",
        "task": meta.get("task"),
        "repo": meta.get("repo"),
        "workflow_preset": meta.get("workflow_preset"),
        "requested_by": meta.get("requested_by"),
        "created_at": item.get("created_at") or meta.get("created_at"),
        "updated_at": item.get("updated_at") or meta.get("updated_at"),
        "requires_approval": meta.get("requires_approval", True),
        "can_execute_now": False,
        "primary_skill": ((meta.get("plan") or {}).get("routed_skill_plan") or {}).get("primary_skill") if isinstance(meta.get("plan"), dict) else None,
        "decision_count": len(meta.get("decision_log") or []) if isinstance(meta.get("decision_log"), list) else 0,
    }


def _title_for_plan(task: str, *, repo: str | None = None, workflow_preset: str | None = None) -> str:
    prefix = "Execution review"
    if repo:
        prefix += f" [{repo}]"
    if workflow_preset:
        prefix += f"/{workflow_preset}"
    return f"{prefix}: {task[:80]}"


def _clean_status(value: str | None) -> str:
    return "_".join((value or "").strip().lower().replace("-", "_").split())


def _safety_note() -> str:
    return "v1.15 stores reviewable plans, decisions and evidence packs only. HIVE does not execute skills, mutate repos, or start background jobs from this queue."


def _evidence_pack_markdown(pack: dict[str, Any]) -> str:
    primary = pack.get("primary_skill") if isinstance(pack.get("primary_skill"), dict) else {}
    lines = [
        "# HIVE Execution Review Evidence Pack",
        "",
        f"- Plan ID: `{pack.get('plan_id')}`",
        f"- Status: `{pack.get('status')}`",
        f"- Task: {pack.get('task')}",
        f"- Repo: {pack.get('repo') or 'not specified'}",
        f"- Workflow preset: {pack.get('workflow_preset') or 'not specified'}",
        f"- Execution mode: `{pack.get('execution_mode')}`",
        f"- Can execute now: `{pack.get('can_execute_now')}`",
        "",
        "## Primary skill",
        "",
        f"- Skill: {primary.get('name') or primary.get('title') or 'none'}",
        f"- Skill ID: {primary.get('skill_id') or primary.get('source_id') or 'none'}",
        f"- Risk: {primary.get('risk_level') or 'unknown'}",
        "",
        "## Candidate skills",
        "",
    ]
    candidates = pack.get("candidate_skills") if isinstance(pack.get("candidate_skills"), list) else []
    if candidates:
        for index, item in enumerate(candidates, start=1):
            if isinstance(item, dict):
                lines.append(f"{index}. {item.get('name') or item.get('title') or item.get('skill_id')} — {item.get('risk_level') or 'unknown'}")
    else:
        lines.append("No candidate skills recorded.")
    lines.extend(["", "## Audit timeline", ""])
    timeline = pack.get("audit_timeline") if isinstance(pack.get("audit_timeline"), list) else []
    if timeline:
        for event in timeline:
            if isinstance(event, dict):
                lines.append(f"- {event.get('at')}: {event.get('event')} / {event.get('status')} by {event.get('actor')} — {event.get('note') or ''}")
    else:
        lines.append("No audit events recorded.")
    lines.extend(["", "## Guardrails", ""])
    guardrails = pack.get("guardrails") if isinstance(pack.get("guardrails"), dict) else {}
    if guardrails:
        for key, value in guardrails.items():
            lines.append(f"- `{key}`: `{value}`")
    else:
        lines.append("No guardrails recorded.")
    lines.extend(["", "HIVE v1.15 evidence packs are review artefacts only. They do not execute skills or mutate repos."])
    return "\n".join(lines) + "\n"
