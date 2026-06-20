from __future__ import annotations

from app.core.version import BUILD_STAGE
from app.services import execution_reviews as reviews


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
        plan_id = (params or [None, None])[1]
        item = self.store.get(plan_id)
        if not item:
            rows = []
        else:
            import json

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


def _fake_plan(**kwargs):
    return {
        "ok": True,
        "task": kwargs["task"],
        "repo": kwargs.get("repo"),
        "workflow_preset": kwargs.get("workflow_preset"),
        "execution_mode": "plan_only",
        "can_execute_now": False,
        "routed_skill_plan": {"primary_skill": {"skill_id": "S194", "name": "podcast-seo"}},
    }


def test_v114_build_stage():
    assert BUILD_STAGE == "v1.25-production-execution-gates"


def test_create_execution_review_dry_run(monkeypatch):
    monkeypatch.setattr(reviews, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(reviews, "shared_execution_plan", _fake_plan)

    result = reviews.create_execution_review_plan(
        settings=_SettingsStub(),
        task="review podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        dry_run=True,
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["status"] == "pending_review"
    assert result["review"]["can_execute_now"] is False


def test_create_list_and_decide_execution_review(monkeypatch):
    _FakeD1.store = {}
    monkeypatch.setattr(reviews, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(reviews, "shared_execution_plan", _fake_plan)

    created = reviews.create_execution_review_plan(
        settings=_SettingsStub(),
        task="review podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        requested_by="tester",
        dry_run=False,
    )
    assert created["ok"] is True
    plan_id = created["plan_id"]

    listed = reviews.list_execution_review_plans(settings=_SettingsStub(), status="pending_review")
    assert listed["ok"] is True
    assert listed["count"] == 1
    assert listed["items"][0]["plan_id"] == plan_id

    decided = reviews.decide_execution_review_plan(
        settings=_SettingsStub(),
        plan_id=plan_id,
        decision="approved",
        reviewer="tester",
        note="manual approval only",
    )
    assert decided["ok"] is True
    assert decided["review"]["status"] == "approved"
    assert decided["review"]["can_execute_now"] is True
    assert decided["review"]["adapter_execution_enabled"] is True
    assert decided["review"]["execution_state"] == "ready_for_execution"
    assert decided["review"]["review_gate"]["approved"] is True

    detail = reviews.get_execution_review_plan(settings=_SettingsStub(), plan_id=plan_id)
    assert detail["ok"] is True
    assert detail["review"]["metadata"]["status"] == "approved"
