from __future__ import annotations

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.services.catalogue_metadata import enrich_task_item
from app.services.execution_adapters import execution_adapter_policy


def shared_execution_plan(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    limit: int = 5,
) -> dict[str, object]:
    """Return a bounded, review-gated execution plan without running tools."""

    del limit
    clean_task = " ".join((task or "").strip().split())[:1200]
    if not clean_task:
        return {"ok": False, "error_code": "missing_task", "message": "task is required."}

    task_metadata = enrich_task_item(
        {
            "id": workflow_preset or "review_queue",
            "summary": clean_task,
        },
        item_id=workflow_preset or "review_queue",
    )
    risk_level = str(task_metadata.get("risk") or "medium")
    requires_approval = bool(task_metadata.get("requires_approval", True))
    policy = execution_adapter_policy(settings)
    steps = [
        {
            "step": 1,
            "name": "classify_task",
            "description": "Confirm repository, workflow intent and risk level.",
        },
        {
            "step": 2,
            "name": "load_sources",
            "description": "Collect relevant repository, storage and database evidence.",
        },
        {
            "step": 3,
            "name": "analyse_evidence",
            "description": "Analyse the supplied evidence and identify the smallest grounded action path.",
        },
        {
            "step": 4,
            "name": "dry_run",
            "description": "Produce a reviewable output or patch plan with no live mutation.",
        },
        {
            "step": 5,
            "name": "approval_gate",
            "description": "Require explicit approval before a production adapter handoff when policy requires it.",
        },
    ]
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "task": clean_task,
        "repo": repo,
        "workflow_preset": workflow_preset,
        "execution_mode": "review_gated_execution",
        "risk_level": risk_level,
        "requires_approval": requires_approval,
        "can_execute_now": False,
        "can_execute_after_approval": bool(policy["enabled"]),
        "adapter_execution_enabled": bool(policy["enabled"]),
        "execution_adapter_policy": policy,
        "shared_steps": steps,
        "guardrails": {
            "no_background_jobs_on_koyeb_free": True,
            "dry_run_first": True,
            "review_queue_required": requires_approval,
            "risk_gates_required": ["medium", "high"],
        },
        "next_adapter_layer": "Production adapters remain allow-listed and approval-gated.",
    }
