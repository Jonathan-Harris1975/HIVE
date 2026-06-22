from __future__ import annotations

from app.core.version import BUILD_STAGE
from app.services import workflow_graphs


class _SettingsStub:
    def public_url_for_r2_lane(self, lane: str, key: str) -> str:
        return "https://skills.example.test"


SETTINGS = _SettingsStub()


def _fake_shared_execution_plan(**kwargs):
    return {
        "ok": True,
        "build_stage_hint": BUILD_STAGE,
        "task": kwargs.get("task"),
        "repo": kwargs.get("repo"),
        "workflow_preset": kwargs.get("workflow_preset"),
        "execution_mode": "plan_only",
        "can_execute_now": False,
        "requires_approval": True,
        "routed_skill_plan": {
            "candidate_skills": [
                {"skill_id": "S194", "title": "podcast-seo", "risk_level": "low"},
                {"skill_id": "S198", "title": "sentry-cli", "risk_level": "medium"},
            ]
        },
    }


def test_v119_build_marker() -> None:
    assert BUILD_STAGE == "v1.26.12-catalogue-metadata"


def test_v118_workflow_templates_are_plan_only() -> None:
    result = workflow_graphs.workflow_graph_templates()
    assert result["ok"] is True
    assert result["build_stage_hint"] == BUILD_STAGE
    assert result["count"] >= 3
    assert result["templates"]["repo_debug"]["free_tier_safe"] is True


def test_v118_workflow_graph_builds_nodes_and_edges(monkeypatch) -> None:
    monkeypatch.setattr(workflow_graphs, "shared_execution_plan", _fake_shared_execution_plan)
    result = workflow_graphs.build_workflow_graph(
        settings=SETTINGS,
        task="review podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        limit=2,
    )
    assert result["ok"] is True
    assert result["build_stage_hint"] == BUILD_STAGE
    assert result["execution_mode"] == "review_gated_production_graph"
    assert result["can_execute_now"] is False
    assert result["node_count"] == 8
    assert result["edge_count"] == 7
    assert result["risk_summary"]["highest_risk"] == "medium"
    assert result["nodes"][-1]["status"] == "approval_required"


def test_v119_execution_preview_readies_adapters_after_approval(monkeypatch) -> None:
    monkeypatch.setattr(workflow_graphs, "shared_execution_plan", _fake_shared_execution_plan)
    result = workflow_graphs.controlled_execution_preview(
        settings=SETTINGS,
        task="triage production monitoring issue",
        repo="AIMS",
        approval_state="approved",
        limit=2,
    )
    assert result["ok"] is True
    assert result["build_stage_hint"] == BUILD_STAGE
    assert result["execution_mode"] == "controlled_production_preview"
    assert result["can_execute_now"] is True
    assert result["adapter_execution_enabled"] is True
    assert result["blocked_count"] == 0
    adapter = [item for item in result["step_statuses"] if item["node_id"] == "adapter_execution"][0]
    assert adapter["status"] == "ready_for_execution"
    assert adapter["can_run"] is True
    assert adapter["blocker"] is None


def test_v119_policies_enable_approved_adapter_handoff() -> None:
    result = workflow_graphs.execution_preview_policies()
    assert result["ok"] is True
    assert result["can_execute_now"] is True
    assert result["adapter_execution_enabled"] is True
    assert result["policies"]["adapter_allowlist_required"]["can_execute_now"] is True
