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
    assert BUILD_STAGE == "v1.22-workflow-simulation-persistence"


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
    assert result["execution_mode"] == "plan_only_graph"
    assert result["can_execute_now"] is False
    assert result["node_count"] == 8
    assert result["edge_count"] == 7
    assert result["risk_summary"]["highest_risk"] == "medium"
    assert result["nodes"][-1]["status"] == "blocked"


def test_v119_execution_preview_blocks_adapters(monkeypatch) -> None:
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
    assert result["execution_mode"] == "controlled_preview_only"
    assert result["can_execute_now"] is False
    assert result["adapter_execution_enabled"] is False
    adapter = [item for item in result["step_statuses"] if item["node_id"] == "adapter_execution"][0]
    assert adapter["status"] == "blocked"
    assert adapter["blocker"] == "execution_adapters_disabled_in_v1_22"


def test_v119_policies_are_non_executing() -> None:
    result = workflow_graphs.execution_preview_policies()
    assert result["ok"] is True
    assert result["can_execute_now"] is False
    assert result["policies"]["adapter_allowlist_required"]["can_execute_now"] is False
