from __future__ import annotations

from app.core.version import BUILD_STAGE
from app.services import workflow_graphs


class _SettingsStub:
    pass


class _FakeD1:
    enabled = True
    store: dict[str, dict] = {}

    def __init__(self, settings):
        self.settings = settings

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        self.store[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "url": url,
            "metadata": metadata,
            "created_at": metadata.get("created_at"),
            "updated_at": metadata.get("updated_at"),
        }
        return {"ok": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [item for item in self.store.values() if lane is None or item["lane"] == lane]
        return {"ok": True, "enabled": True, "count": len(items), "items": items[:limit]}

    def query(self, sql, params=None):
        import json

        preview_id = (params or [None, None])[1]
        item = self.store.get(preview_id)
        rows = []
        if item:
            rows = [{
                "id": item["id"],
                "lane": item["lane"],
                "source_type": item["source_type"],
                "source_id": item["source_id"],
                "title": item["title"],
                "url": item["url"],
                "metadata_json": json.dumps(item["metadata"]),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }]
        return {"ok": True, "result": [{"results": rows}]}


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
                {"skill_id": "S194", "title": "podcast-seo", "risk_level": "low", "repos": ["AIMS", "Website"]},
                {"skill_id": "S198", "title": "sentry-cli", "risk_level": "medium", "repos": ["AIMS", "RAMS"]},
            ]
        },
    }


def test_v122_build_marker():
    assert BUILD_STAGE == "v1.26.11-env-split"


def test_v121_policy_profiles_enable_approved_handoff():
    result = workflow_graphs.execution_policy_profiles()
    assert result["ok"] is True
    assert result["can_execute_now"] is True
    assert result["adapter_execution_enabled"] is True
    assert result["profiles"]["human_approval_required"]["requires_human_approval"] is True
    assert result["profiles"]["r2_write_allowed"]["allows_r2_write"] is True


def test_v122_workflow_simulation_waits_for_approval(monkeypatch):
    monkeypatch.setattr(workflow_graphs, "shared_execution_plan", _fake_shared_execution_plan)
    result = workflow_graphs.simulate_workflow_execution(
        settings=_SettingsStub(),
        task="review the podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        approval_state="pending_review",
        policy_profile="review_required",
    )
    assert result["ok"] is True
    assert result["execution_mode"] == "approval_gated_simulation"
    assert result["can_execute_now"] is False
    assert result["adapter_execution_enabled"] is True
    assert result["estimated_cost"]["estimated_model_calls"] == 0
    assert result["missing_prerequisites"] == ["human_review_approval"]


def test_v120_save_and_read_preview(monkeypatch):
    _FakeD1.store = {}
    monkeypatch.setattr(workflow_graphs, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(workflow_graphs, "shared_execution_plan", _fake_shared_execution_plan)
    saved = workflow_graphs.save_execution_preview(
        settings=_SettingsStub(),
        task="review the podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        requested_by="tester",
        dry_run=False,
    )
    assert saved["ok"] is True
    assert saved["can_execute_now"] is False
    preview_id = saved["preview_id"]

    history = workflow_graphs.list_saved_execution_previews(settings=_SettingsStub())
    assert history["ok"] is True
    assert history["count"] == 1
    assert history["items"][0]["preview_id"] == preview_id

    detail = workflow_graphs.get_saved_execution_preview(settings=_SettingsStub(), preview_id=preview_id)
    assert detail["ok"] is True
    assert detail["preview"]["metadata"]["preview_id"] == preview_id
