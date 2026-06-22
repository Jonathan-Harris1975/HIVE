from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.services.execution_adapters import execution_adapter_policy
from app.services.catalogue_metadata import enrich_task_item
from app.services.skill_registry import shared_execution_plan
from app.storage.d1 import D1MetadataStore

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

EXECUTION_PREVIEW_LANE = "execution_previews"
EXECUTION_PREVIEW_SOURCE_TYPE = "execution_preview"

CONTROLLED_EXECUTION_POLICIES = {
    "plan_only": {
        "enabled": True,
        "can_execute_now": False,
        "description": "Generate a reviewable plan without mutating systems.",
    },
    "review_gate_required": {
        "enabled": True,
        "can_execute_now": False,
        "can_execute_after_approval": True,
        "description": "A human review decision is required before an allow-listed production adapter handoff can run.",
    },
    "adapter_allowlist_required": {
        "enabled": True,
        "can_execute_now": True,
        "description": "Production execution adapters are explicit, allow-listed and unlocked by approval.",
    },
    "koyeb_bounded_runtime": {
        "enabled": True,
        "can_execute_now": True,
        "description": "Production handoff remains bounded: no long loops, package installs or unreviewed background jobs.",
    },
}

POLICY_PROFILES: dict[str, dict[str, object]] = {
    "readonly": {
        "label": "Read-only",
        "can_execute_now": False,
        "can_execute_after_approval": False,
        "allows_repo_mutation": False,
        "allows_r2_write": False,
        "requires_human_approval": False,
        "description": "Inspection, search, evidence-pack and simulation only.",
    },
    "review_required": {
        "label": "Review required",
        "can_execute_now": True,
        "can_execute_after_approval": True,
        "allows_repo_mutation": False,
        "allows_r2_write": False,
        "requires_human_approval": True,
        "description": "Approval unlocks the allow-listed production adapter handoff.",
    },
    "repo_safe": {
        "label": "Repo safe",
        "can_execute_now": True,
        "can_execute_after_approval": True,
        "allows_repo_mutation": False,
        "allows_r2_write": False,
        "requires_human_approval": True,
        "description": "May produce approved repo-change handoff artefacts, but does not push commits or install packages.",
    },
    "r2_write_allowed": {
        "label": "R2 write allowed",
        "can_execute_now": True,
        "can_execute_after_approval": True,
        "allows_repo_mutation": False,
        "allows_r2_write": True,
        "requires_human_approval": True,
        "description": "Allows approved export/write handoff where the configured HIVE lane is writable.",
    },
    "human_approval_required": {
        "label": "Human approval required",
        "can_execute_now": True,
        "can_execute_after_approval": True,
        "allows_repo_mutation": False,
        "allows_r2_write": False,
        "requires_human_approval": True,
        "description": "Conservative default for medium/high-risk plans; approval unlocks the controlled adapter gate.",
    },
}



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def workflow_graph_templates() -> dict[str, object]:
    """Return static workflow graph templates for the future UI."""

    templates = {
        template_id: enrich_task_item({"id": template_id, **template}, item_id=template_id)
        for template_id, template in WORKFLOW_GRAPH_TEMPLATES.items()
    }
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "count": len(templates),
        "templates": templates,
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

    Build a graph model for HIVE-UI review and production handoff. The graph
    itself does not mutate systems; an approved preview marks the allow-listed
    adapter handoff as ready.
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
        "execution_mode": "review_gated_production_graph",
        "can_execute_now": False,
        "requires_approval": True,
        "adapter_execution_enabled": bool(execution_adapter_policy(settings)["enabled"]),
        "execution_adapter_policy": execution_adapter_policy(settings),
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

    Model the approved production adapter gate for HIVE-UI. Pending plans still
    require review; approved plans now surface the allow-listed adapter handoff
    as ready rather than blocked.
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
    adapter_policy = execution_adapter_policy(settings)
    can_execute_now = bool(approval == "approved" and adapter_policy["enabled"])
    statuses = _execution_step_statuses(
        graph,
        approval_state=approval,
        adapter_enabled=bool(adapter_policy["enabled"]),
    )
    blocked = [item for item in statuses if item.get("status") in {"blocked", "review_required"}]
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "preview_id": f"execution-preview-{uuid4()}",
        "task": graph.get("task"),
        "repo": graph.get("repo"),
        "workflow_preset": workflow_preset,
        "template": graph.get("template"),
        "execution_mode": "controlled_production_preview",
        "approval_state": approval,
        "can_execute_now": can_execute_now,
        "requires_approval": approval != "approved",
        "adapter_execution_enabled": bool(adapter_policy["enabled"]),
        "execution_state": "ready_for_execution" if can_execute_now else "awaiting_approval",
        "execution_adapter_policy": adapter_policy,
        "step_count": len(statuses),
        "blocked_count": len(blocked),
        "step_statuses": statuses,
        "policies": CONTROLLED_EXECUTION_POLICIES,
        "workflow_graph": graph,
        "next_required_actions": _next_required_actions(
            approval,
            blocked,
            adapter_enabled=bool(adapter_policy["enabled"]),
        ),
        "safety_note": _safety_note(),
    }


def execution_preview_policies() -> dict[str, object]:
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "policies": CONTROLLED_EXECUTION_POLICIES,
        "can_execute_now": True,
        "adapter_execution_enabled": True,
        "can_execute_after_approval": True,
        "note": "Production adapter handoff is available for approved, allow-listed review plans.",
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
    nodes = [
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
            "summary": "A review decision is required before the production adapter handoff can run.",
        },
        {
            "id": "adapter_execution",
            "type": "controlled_execution",
            "label": "Production adapter handoff",
            "status": "approval_required",
            "summary": "Available after approval through the production adapter allow-list.",
        },
    ]
    return [enrich_task_item(node, item_id=str(node.get("id") or "")) for node in nodes]


def _workflow_edges(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    edges: list[dict[str, object]] = []
    for left, right in zip(nodes, nodes[1:]):
        edges.append({"from": left["id"], "to": right["id"], "type": "sequential"})
    return edges


def _execution_step_statuses(
    graph: dict[str, object], *, approval_state: str, adapter_enabled: bool
) -> list[dict[str, object]]:
    statuses = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        status = str(node.get("status") or "planned")
        can_run = False
        blocker = None
        if node_id == "adapter_execution":
            if approval_state == "approved" and adapter_enabled:
                status = "ready_for_execution"
                can_run = True
            elif not adapter_enabled:
                status = "blocked"
                blocker = "execution_adapters_disabled_by_config"
            else:
                status = "review_required"
                blocker = "human_review_required"
        elif node_id in {"risk_gate", "review_queue"}:
            status = "approved" if approval_state == "approved" else "review_required"
            blocker = None if approval_state == "approved" else "human_review_required"
        elif node_id == "request":
            status = "complete"
        statuses.append(
            enrich_task_item(
                {
                    "id": node_id,
                    "node_id": node_id,
                    "label": node.get("label"),
                    "type": node.get("type"),
                    "status": status,
                    "can_run": can_run,
                    "blocker": blocker,
                    "summary": node.get("summary"),
                    "description": node.get("description") or node.get("summary"),
                },
                item_id=node_id,
            )
        )
    return statuses


def _normalise_approval_state(value: str | None) -> str:
    candidate = (value or "pending_review").strip().lower().replace("-", "_")
    if candidate in {"approved", "rejected", "needs_changes", "archived", "pending_review"}:
        return candidate
    return "pending_review"


def _next_required_actions(
    approval_state: str, blocked: list[dict[str, object]], *, adapter_enabled: bool
) -> list[str]:
    actions = []
    if approval_state != "approved":
        actions.append("Approve the execution review decision to unlock the allow-listed production adapter handoff.")
    elif adapter_enabled:
        actions.append("Approved plan is ready for operator-triggered production adapter handoff.")
    if blocked and not adapter_enabled:
        actions.append("Set EXECUTION_ADAPTERS_ENABLED=true to enable the production adapter gate.")
    actions.append("Use the evidence pack endpoint to review sources, candidate skills and risk notes.")
    return actions


def _safety_note() -> str:
    return (
        "Production approval unlocks allow-listed, operator-triggered adapter handoff. "
        "Decision and preview endpoints do not auto-run package installs, repo pushes or background jobs."
    )



def execution_policy_profiles() -> dict[str, object]:
    """Return reusable approval/policy profiles for the future operator UI."""

    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "count": len(POLICY_PROFILES),
        "profiles": POLICY_PROFILES,
        "can_execute_now": True,
        "adapter_execution_enabled": True,
        "can_execute_after_approval": True,
        "note": "Policy profiles now describe approved production adapter handoff gates.",
    }


def simulate_workflow_execution(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    template: str | None = None,
    limit: int = 5,
    approval_state: str | None = None,
    policy_profile: str | None = None,
) -> dict[str, object]:
    """Run a pretend-mode simulation over a controlled execution preview.

    The simulation estimates services, risk, cost class and blockers. It does
    not execute adapters, mutate repos, write R2 objects or start background
    work. It is intentionally deterministic and cheap for Koyeb Free.
    """

    preview = controlled_execution_preview(
        settings=settings,
        task=task,
        repo=repo,
        workflow_preset=workflow_preset,
        template=template,
        limit=limit,
        approval_state=approval_state,
    )
    if not preview.get("ok"):
        return preview
    graph = preview.get("workflow_graph") if isinstance(preview.get("workflow_graph"), dict) else {}
    risk_summary = graph.get("risk_summary") if isinstance(graph.get("risk_summary"), dict) else {}
    candidate_skills = graph.get("candidate_skills") if isinstance(graph.get("candidate_skills"), list) else []
    selected_profile = _normalise_policy_profile(policy_profile, risk_summary)
    required_services = _required_services_for_graph(graph)
    cost_estimate = _simulation_cost_estimate(candidate_skills, required_services)
    can_execute_now = bool(preview.get("can_execute_now"))
    adapter_enabled = bool(preview.get("adapter_execution_enabled"))
    missing_prerequisites = _simulation_missing_prerequisites(
        preview,
        selected_profile,
        adapter_enabled=adapter_enabled,
    )
    rollback_notes = _simulation_rollback_notes(required_services)
    affected = _affected_surfaces(preview, graph)
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "simulation_id": f"execution-simulation-{uuid4()}",
        "preview_id": preview.get("preview_id"),
        "task": preview.get("task"),
        "repo": preview.get("repo"),
        "workflow_preset": workflow_preset,
        "policy_profile": selected_profile,
        "policy": POLICY_PROFILES[selected_profile],
        "execution_mode": "approved_controlled_execution_simulation" if can_execute_now else "approval_gated_simulation",
        "can_execute_now": can_execute_now,
        "adapter_execution_enabled": adapter_enabled,
        "execution_state": "ready_for_execution" if can_execute_now else "awaiting_approval",
        "required_services": required_services,
        "affected_repos": affected["repos"],
        "affected_buckets": affected["buckets"],
        "risk_summary": risk_summary,
        "estimated_cost": cost_estimate,
        "missing_prerequisites": missing_prerequisites,
        "rollback_notes": rollback_notes,
        "workflow_graph": graph,
        "controlled_preview": preview,
        "next_required_actions": preview.get("next_required_actions", []) + [
            "Save the preview if it needs to appear in the operator review history.",
            "Use only the configured allow-listed adapter handoff for approved production runs.",
        ],
        "safety_note": _safety_note(),
    }


def save_execution_preview(
    *,
    settings: Settings,
    task: str,
    repo: str | None = None,
    workflow_preset: str | None = None,
    template: str | None = None,
    limit: int = 5,
    approval_state: str | None = None,
    requested_by: str | None = None,
    policy_profile: str | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Persist a controlled execution preview/simulation record in D1.

    Dry-run returns the record that would be saved. Live mode stores D1 metadata
    only; it does not execute anything.
    """

    simulation = simulate_workflow_execution(
        settings=settings,
        task=task,
        repo=repo,
        workflow_preset=workflow_preset,
        template=template,
        limit=limit,
        approval_state=approval_state,
        policy_profile=policy_profile,
    )
    if not simulation.get("ok"):
        return simulation
    now = _now()
    preview_id = str(simulation.get("preview_id") or f"execution-preview-{uuid4()}")
    metadata = {
        "preview_id": preview_id,
        "simulation_id": simulation.get("simulation_id"),
        "task": simulation.get("task"),
        "repo": simulation.get("repo"),
        "workflow_preset": workflow_preset,
        "template": template,
        "requested_by": requested_by or "hive-user",
        "approval_state": approval_state or "pending_review",
        "policy_profile": simulation.get("policy_profile"),
        "status": "preview_saved" if not dry_run else "dry_run",
        "created_at": now,
        "updated_at": now,
        "execution_mode": "persisted_execution_preview",
        "can_execute_now": bool(simulation.get("can_execute_now")),
        "adapter_execution_enabled": bool(simulation.get("adapter_execution_enabled")),
        "simulation": simulation,
    }
    item_id = preview_id
    title = _preview_title(str(simulation.get("task") or preview_id))
    if dry_run:
        return {
            "ok": True,
            "enabled": True,
            "dry_run": True,
            "build_stage_hint": BUILD_STAGE,
            "lane": EXECUTION_PREVIEW_LANE,
            "preview_id": preview_id,
            "would_save": metadata,
            "can_execute_now": bool(metadata.get("can_execute_now")),
            "safety_note": _safety_note(),
        }
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}
    result = d1.upsert_metadata(
        item_id=item_id,
        lane=EXECUTION_PREVIEW_LANE,
        source_type=EXECUTION_PREVIEW_SOURCE_TYPE,
        source_id=preview_id,
        title=title,
        url=None,
        metadata=metadata,
    )
    return {
        "ok": bool(result.get("ok")),
        "enabled": True,
        "dry_run": False,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_PREVIEW_LANE,
        "preview_id": preview_id,
        "d1_result": result,
        "preview": metadata,
        "can_execute_now": bool(metadata.get("can_execute_now")),
        "safety_note": _safety_note(),
    }


def list_saved_execution_previews(
    *,
    settings: Settings,
    repo: str | None = None,
    policy_profile: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}
    result = d1.list_metadata(lane=EXECUTION_PREVIEW_LANE, limit=limit)
    if not result.get("ok"):
        return result
    items = []
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        summary = _preview_summary(item)
        if repo and str(summary.get("repo") or "").lower() != repo.lower():
            continue
        if policy_profile and str(summary.get("policy_profile") or "").lower() != policy_profile.lower():
            continue
        items.append(summary)
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_PREVIEW_LANE,
        "count": len(items),
        "items": items,
        "can_execute_now": any(bool(item.get("can_execute_now")) for item in items),
        "safety_note": _safety_note(),
    }


def get_saved_execution_preview(*, settings: Settings, preview_id: str) -> dict[str, object]:
    d1 = D1MetadataStore(settings)
    if not d1.enabled:
        return {"ok": False, "enabled": False, "error_code": "d1_disabled"}
    item = _get_preview_item(d1, preview_id)
    if not item:
        return {"ok": False, "enabled": True, "error_code": "execution_preview_not_found", "preview_id": preview_id}
    return {
        "ok": True,
        "enabled": True,
        "build_stage_hint": BUILD_STAGE,
        "lane": EXECUTION_PREVIEW_LANE,
        "preview_id": preview_id,
        "preview": item,
        "can_execute_now": bool((item.get("metadata") or {}).get("can_execute_now")) if isinstance(item.get("metadata"), dict) else False,
        "safety_note": _safety_note(),
    }


def _get_preview_item(d1: D1MetadataStore, preview_id: str) -> dict[str, Any] | None:
    result = d1.query(
        """
        SELECT id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at
        FROM hive_ecosystem_metadata
        WHERE lane = ? AND id = ?
        LIMIT 1
        """,
        [EXECUTION_PREVIEW_LANE, preview_id],
    )
    if not result.get("ok"):
        return None
    for row in _extract_d1_rows(result.get("result")):
        row["metadata"] = _json_or_none(row.pop("metadata_json", None))
        return row
    return None


def _extract_d1_rows(result: Any) -> list[dict[str, Any]]:
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


def _preview_title(task: str) -> str:
    trimmed = " ".join(task.split())[:96]
    return f"Execution preview: {trimmed}" if trimmed else "Execution preview"


def _preview_summary(item: dict[str, Any]) -> dict[str, object]:
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    simulation = meta.get("simulation") if isinstance(meta.get("simulation"), dict) else {}
    return {
        "id": item.get("id"),
        "preview_id": meta.get("preview_id") or item.get("id"),
        "simulation_id": meta.get("simulation_id"),
        "title": item.get("title"),
        "task": meta.get("task"),
        "repo": meta.get("repo"),
        "workflow_preset": meta.get("workflow_preset"),
        "policy_profile": meta.get("policy_profile"),
        "approval_state": meta.get("approval_state"),
        "status": meta.get("status"),
        "risk_summary": simulation.get("risk_summary") if isinstance(simulation.get("risk_summary"), dict) else {},
        "estimated_cost": simulation.get("estimated_cost") if isinstance(simulation.get("estimated_cost"), dict) else {},
        "can_execute_now": bool(meta.get("can_execute_now")),
        "adapter_execution_enabled": bool(meta.get("adapter_execution_enabled")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


def _normalise_policy_profile(value: str | None, risk_summary: dict[str, object]) -> str:
    candidate = (value or "").strip().lower().replace("-", "_")
    if candidate in POLICY_PROFILES:
        return candidate
    highest = str(risk_summary.get("highest_risk") or "unknown").lower()
    if highest in {"medium", "high", "unknown"}:
        return "human_approval_required"
    return "readonly"


def _required_services_for_graph(graph: dict[str, object]) -> list[dict[str, object]]:
    services = [
        {"service": "D1", "purpose": "metadata, review records and saved previews", "required": True},
        {"service": "PostgreSQL", "purpose": "conversation/file/chunk memory where relevant", "required": False},
        {"service": "R2", "purpose": "source artefacts/evidence packs where already configured", "required": False},
        {"service": "Vectorize", "purpose": "semantic retrieval over chunks/skills when available", "required": False},
        {"service": "OpenRouter", "purpose": "answer/summary generation outside preview-only simulation", "required": False},
    ]
    candidate_count = len(graph.get("candidate_skills", []) if isinstance(graph.get("candidate_skills"), list) else [])
    if candidate_count:
        services.append({"service": "hive-skills R2 lane", "purpose": "skill descriptors and registry metadata", "required": True})
    return services


def _simulation_cost_estimate(candidate_skills: list[dict[str, object]], required_services: list[dict[str, object]]) -> dict[str, object]:
    skill_count = len(candidate_skills)
    service_count = len(required_services)
    if skill_count <= 2:
        cost_class = "low"
    elif skill_count <= 8:
        cost_class = "medium"
    else:
        cost_class = "high"
    return {
        "cost_class": cost_class,
        "estimated_model_calls": 0,
        "estimated_d1_reads": max(1, skill_count),
        "estimated_r2_reads": 0,
        "estimated_vector_queries": 0,
        "service_touch_count": service_count,
        "note": "Simulation is deterministic and does not call models, write R2, or run adapters.",
    }


def _simulation_missing_prerequisites(
    preview: dict[str, object], policy_profile: str, *, adapter_enabled: bool
) -> list[str]:
    missing = []
    if preview.get("approval_state") != "approved":
        missing.append("human_review_approval")
    if policy_profile != "readonly" and not adapter_enabled:
        missing.append("execution_adapters_disabled_by_config")
    return missing


def _simulation_rollback_notes(required_services: list[dict[str, object]]) -> list[str]:
    notes = ["Preview/simulation rollback is not required because the endpoint does not auto-run external mutation."]
    touched = [item.get("service") for item in required_services]
    if "D1" in touched:
        notes.append("Saved previews can be removed from the D1 execution_previews lane if needed.")
    return notes


def _affected_surfaces(preview: dict[str, object], graph: dict[str, object]) -> dict[str, list[str]]:
    repos = []
    repo = preview.get("repo") or graph.get("repo")
    if repo:
        repos.append(str(repo))
    candidates = graph.get("candidate_skills") if isinstance(graph.get("candidate_skills"), list) else []
    for item in candidates:
        if isinstance(item, dict):
            for repo_name in item.get("repos", []) if isinstance(item.get("repos"), list) else []:
                name = str(repo_name)
                if name not in repos:
                    repos.append(name)
    buckets = ["hive-skills"] if candidates else []
    return {"repos": repos[:10], "buckets": buckets}
