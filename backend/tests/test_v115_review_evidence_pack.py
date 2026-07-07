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
        created_at = metadata.get("created_at") or self.store.get(item_id, {}).get("created_at")
        self.store[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "url": url,
            "metadata": metadata,
            "created_at": created_at,
            "updated_at": metadata.get("updated_at"),
        }
        return {"ok": True}

    def query(self, sql, params=None):
        plan_id = (params or [None, None])[1]
        item = self.store.get(plan_id)
        import json
        rows = [] if not item else [{
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
        "routed_skill_plan": {
            "primary_skill": {"skill_id": "S194", "name": "podcast-seo", "risk_level": "low"},
            "candidate_skills": [{"skill_id": "S194", "name": "podcast-seo", "risk_level": "low"}],
        },
        "shared_steps": [{"step": 1, "name": "approval_gate"}],
        "guardrails": {"no_auto_install": True},
    }


def test_v115_build_stage():
    assert BUILD_STAGE == "v1.30-repository-qa-through-documentation"


def test_evidence_pack_audit_trail_and_exports(monkeypatch):
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
    plan_id = created["plan_id"]
    reviews.decide_execution_review_plan(
        settings=_SettingsStub(),
        plan_id=plan_id,
        decision="needs_changes",
        reviewer="tester",
        note="add evidence pack",
    )

    audit = reviews.execution_review_audit_trail(settings=_SettingsStub(), plan_id=plan_id)
    assert audit["ok"] is True
    assert audit["decision_count"] == 1
    assert audit["timeline"][0]["event"] == "created"
    assert audit["timeline"][1]["status"] == "needs_changes"

    pack = reviews.execution_review_evidence_pack(settings=_SettingsStub(), plan_id=plan_id)
    assert pack["ok"] is True
    assert pack["evidence_pack"]["plan_id"] == plan_id
    assert pack["evidence_pack"]["execution_mode"] == "review_gated_execution"
    assert pack["evidence_pack"]["can_execute_now"] is False
    assert pack["evidence_pack"]["candidate_count"] == 1

    exported = reviews.export_execution_review_pack(settings=_SettingsStub(), plan_id=plan_id, export_format="markdown")
    assert exported["ok"] is True
    assert exported["format"] == "markdown"
    assert exported["storage"] == "inline_response_only"
    assert "HIVE Execution Review Evidence Pack" in exported["export_document"]
