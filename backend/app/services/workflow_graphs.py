from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.services.skill_registry import shared_execution_plan

WORKFLOW_GRAPH_TEMPLATES: dict[str, dict[str, object]] = {
    "audit_review": {
        "label": "Audit review workflow",
        "description": "Review RAMS/AIMS audit bundles, collect evidence, propose future-facing fixes, and queue review.",
        "recommended_presets": ["audit_report_review", "ci_log_analysis"],
        "default_repo": "RAMS",
        "free_tier_safe": True,
    },
    "repo_debug": {
        "label": "Repo/debug workflow",
        "description": "Classify repo/log issues, identify candidate skills, and produce a review-gated patch plan.",
        "recommended_presets": ["repo_debug_bundle", "ci_log_analysis"],
        "default_repo": "HIVE",
        "free_tier_safe": True,
    },
    "content_qa": {
        "label": "Content QA workflow",
        "description": "Review social, blog, RSS, podcast or eBook content against brand and QA gates.",
        "recommended_presets": ["social_content_qa", "podcast_episode_review", "ebook_keyword_review"],
        "default_repo": "AIMS",
        "free_tier_safe": True,
    },
    "skills_registry": {
        "label": "Skills registry workflow",
        "description": "Search, recommend, route and review shared skills without installing or executing them.",
        "recommended_presets": [],
        "default_repo": "HIVE",
        "free_tier_safe": True,
    },
}

CONTROLLED_EXECUTION_POLICIES = {
    "plan_only": {
        "enabled": True,
        "can_execute_now": False,
        "description": "Generate a reviewable plan without running skills or mutating systems.",
    },
    "review_gate_required": {
        "enabled": True,
        "can_execute_now": False,
        "description": "A human review decision is required before any future adapter can run.",
    },
    "adapter_allowlist_required": {
        "enabled": True,
        "can_execute_now": False,
        "description": "Future execution adapters must be explicit, allow-listed and dry-run first.",
    },
    "koyeb_free_safe": {
        "enabled": True,
        "can_execute_now": False,
        "description": "No background jobs, long loops, installs, repo mutations or large batch execution on Koyeb Free.",
    },
}


def workflow_graph_templates() -> dict[str, object]:
    """Return static workflow graph templates for the future UI."""

    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "count": len(WORKFLOW_GRAPH_TEMPLATES),
        "templates": WORKFLOW_GRAPH_TEMPLATES,
        "note": "Templates are UI/planning hints only. They do not execute skills or mutate repos.",
    }


def build_workflow_graph(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    template: str | None = None,
    limit: int = 5,
) -> dict[str, object]:
    """Build a graph-shaped, review-gated workflow plan.

    v1.18 adds a graph model inspired by lightweight workflow/run-plan tools,
    but keeps HIVE firmly plan-only. The graph is designed for UI rendering and
    review decisions, not execution.
    """

    clean_task = " ".join((task or "").strip().split())[:1200]
    if not clean_task:
        return {"ok": False, "error_code": "missing_task", "message": "task is required."}

    selected_template = _normalise_template(template, workflow_preset, repo)
    plan = shared_execution_plan(
        settings=settings,
        task=clean_task,
        repo=repo or str(WORKFLOW_GRAPH_TEMPLATES[selected_template].get("default_repo") or "HIVE"),
        workflow_preset=workflow_preset,
        limit=limit,
    )
    if not plan.get("ok"):
        return plan

    graph_id = f"workflow-graph-{uuid4()}"
    candidate_skills = _candidate_skills_from_plan(plan)
    risk_summary = _risk_summary(candidate_skills)
    nodes = _workflow_nodes(
        task=clean_task,
        template=selected_template,
        plan=plan,
        candidate_skills=candidate_skills,
        risk_summary=risk_summary,
    )
    edges = _workflow_edges(nodes)
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "graph_id": graph_id,
        "template": selected_template,
        "task": clean_task,
        "repo": plan.get("repo"),
        "workflow_preset": workflow_preset,
        "execution_mode": "plan_only_graph",
        "can_execute_now": False,
        "requires_approval": True,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "risk_summary": risk_summary,
        "candidate_skills": candidate_skills,
        "source_plan": plan,
        "free_tier_note": "Graph building is synchronous and bounded; no background execution starts on Koyeb Free.",
        "safety_note": _safety_note(),
    }


def controlled_execution_preview(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    template: str | None = None,
    limit: int = 5,
    approval_state: str | None = None,
) -> dict[str, object]:
    """Return a controlled execution preview with step statuses.

    v1.19 is still non-executing. It models what would be required for future
    execution adapters and clearly marks every runnable-looking step as blocked
    by review/adapter gates.
    """

    graph = build_workflow_graph(
        settings=settings,
        task=task,
        repo=repo,
        workflow_preset=workflow_preset,
        template=template,
        limit=limit,
    )
    if not graph.get("ok"):
        return graph

    approval = _normalise_approval_state(approval_state)
    statuses = _execution_step_statuses(graph, approval_state=approval)
    blocked = [item for item in statuses if item.get("status") in {"blocked", "review_required"}]
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "preview_id": f"execution-preview-{uuid4()}",
        "task": graph.get("task"),
        "repo": graph.get("repo"),
        "workflow_preset": workflow_preset,
        "template": graph.get("template"),
        "execution_mode": "controlled_preview_only",
        "approval_state": approval,
        "can_execute_now": False,
        "requires_approval": True,
        "adapter_execution_enabled": False,
        "step_count": len(statuses),
        "blocked_count": len(blocked),
        "step_statuses": statuses,
        "policies": CONTROLLED_EXECUTION_POLICIES,
        "workflow_graph": graph,
        "next_required_actions": _next_required_actions(approval, blocked),
        "safety_note": _safety_note(),
    }


def execution_preview_policies() -> dict[str, object]:
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "policies": CONTROLLED_EXECUTION_POLICIES,
        "can_execute_now": False,
        "note": "v1.19 exposes execution preview semantics only. Adapter execution remains unavailable.",
    }


def _normalise_template(template: str | None, workflow_preset: str | None, repo: str | None) -> str:
    candidate = (template or "").strip().lower().replace("-", "_")
    if candidate in WORKFLOW_GRAPH_TEMPLATES:
        return candidate
    preset = (workflow_preset or "").strip().lower()
    if "audit" in preset:
        return "audit_review"
    if "repo" in preset or "ci" in preset or (repo or "").strip().lower() == "hive":
        return "repo_debug"
    if "social" in preset or "podcast" in preset or "ebook" in preset:
        return "content_qa"
    if "skill" in preset:
        return "skills_registry"
    return "skills_registry"


def _candidate_skills_from_plan(plan: dict[str, object]) -> list[dict[str, object]]:
    routed = plan.get("routed_skill_plan") if isinstance(plan.get("routed_skill_plan"), dict) else {}
    candidates = routed.get("candidate_skills") if isinstance(routed.get("candidate_skills"), list) else []
    return [item for item in candidates if isinstance(item, dict)][:25]


def _risk_summary(candidate_skills: list[dict[str, object]]) -> dict[str, object]:
    counts = {"low": 0, "medium": 0, "high": 0, "unknown": 0}
    for item in candidate_skills:
        risk = str(item.get("risk_level") or "unknown").strip().lower()
        counts[risk if risk in counts else "unknown"] += 1
    highest = "high" if counts["high"] else "medium" if counts["medium"] else "low" if counts["low"] else "unknown"
    return {
        "candidate_count": len(candidate_skills),
        "by_risk": counts,
        "highest_risk": highest,
        "review_required": highest in {"medium", "high", "unknown"} or bool(candidate_skills),
    }


def _workflow_nodes(
    *,
    task: str,
    template: str,
    plan: dict[str, object],
    candidate_skills: list[dict[str, object]],
    risk_summary: dict[str, object],
) -> list[dict[str, object]]:
    return [
        {
            "id": "request",
            "type": "input",
            "label": "Operator request",
            "status": "complete",
            "summary": task,
        },
        {
            "id": "classify",
            "type": "decision",
            "label": "Classify workflow",
            "status": "planned",
            "summary": f"Template: {template}; repo: {plan.get('repo') or 'unspecified'}.",
        },
        {
            "id": "recommend_skills",
            "type": "skill_selection",
            "label": "Recommend skills",
            "status": "planned",
            "summary": f"{len(candidate_skills)} candidate skill(s) selected from the D1 registry.",
            "skill_ids": [item.get("skill_id") for item in candidate_skills if item.get("skill_id")],
        },
        {
            "id": "collect_evidence",
            "type": "evidence",
            "label": "Collect evidence",
            "status": "planned",
            "summary": "Use R2 lanes, D1 metadata, SQL chunks and Vectorize only where already configured.",
        },
        {
            "id": "dry_run_output",
            "type": "dry_run",
            "label": "Generate dry-run output",
            "status": "planned",
            "summary": "Produce a reviewable output or patch plan without live changes.",
        },
        {
            "id": "risk_gate",
            "type": "gate",
            "label": "Risk gate",
            "status": "review_required" if risk_summary.get("review_required") else "planned",
            "summary": f"Highest candidate risk: {risk_summary.get('highest_risk')}.",
            "risk_summary": risk_summary,
        },
        {
            "id": "review_queue",
            "type": "approval",
            "label": "Execution review queue",
            "status": "review_required",
            "summary": "A review decision is required before any future adapter could run.",
        },
        {
            "id": "adapter_execution",
            "type": "blocked_execution",
            "label": "Future adapter execution",
            "status": "blocked",
            "summary": "Blocked in v1.19. No adapters, repo mutations, installs or background jobs are enabled.",
        },
    ]


def _workflow_edges(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    for left, right in zip(nodes, nodes[1:]):
        edges.append({"from": left["id"], "to": right["id"], "type": "sequential"})
    return edges


def _execution_step_statuses(graph: dict[str, object], *, approval_state: str) -> list[dict[str, object]]:
    statuses = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        status = str(node.get("status") or "planned")
        can_run = False
        blocker = None
        if node_id == "adapter_execution":
            status = "blocked"
            blocker = "execution_adapters_disabled_in_v1_19"
        elif node_id in {"risk_gate", "review_queue"}:
            status = "review_required" if approval_state != "approved" else "approved_but_execution_still_disabled"
            blocker = None if approval_state == "approved" else "human_review_required"
        elif node_id == "request":
            status = "complete"
        statuses.append(
            {
                "node_id": node_id,
                "label": node.get("label"),
                "type": node.get("type"),
                "status": status,
                "can_run": can_run,
                "blocker": blocker,
                "summary": node.get("summary"),
            }
        )
    return statuses


def _normalise_approval_state(value: str | None) -> str:
    candidate = (value or "pending_review").strip().lower().replace("-", "_")
    if candidate in {"approved", "rejected", "needs_changes", "archived", "pending_review"}:
        return candidate
    return "pending_review"


def _next_required_actions(approval_state: str, blocked: list[dict[str, object]]) -> list[str]:
    actions = []
    if approval_state != "approved":
        actions.append("Create or update an execution review decision before any future adapter can run.")
    if blocked:
        actions.append("Keep execution adapters disabled until an explicit allow-list and dry-run adapter layer exists.")
    actions.append("Use the evidence pack endpoint to review sources, candidate skills and risk notes.")
    return actions


def _safety_note() -> str:
    return (
        "v1.19 is controlled-preview only. HIVE does not execute skills, mutate repos, "
        "install packages, write exports, or start background jobs from these endpoints."
    )
