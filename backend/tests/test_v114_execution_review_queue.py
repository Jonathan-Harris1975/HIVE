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
        "execution_mode": "review_gated_execution",
        "can_execute_now": False,
        "risk_level": "medium",
        "requires_approval": True,
    }


def test_v114_build_stage():
    assert BUILD_STAGE == "v1.31-production-readiness"


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


def test_execution_review_preserves_preview_provenance(monkeypatch):
    _FakeD1.store = {}
    monkeypatch.setattr(reviews, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(reviews, "shared_execution_plan", _fake_plan)

    _FakeD1.store["execution-preview-123"] = {
        "id": "execution-preview-123",
        "lane": "execution_previews",
        "source_type": "execution_preview",
        "source_id": "execution-preview-123",
        "title": "Execution preview: review podcast SEO workflow",
        "url": None,
        "metadata": {
            "preview_id": "execution-preview-123",
            "simulation_id": "execution-simulation-456",
            "task": "review podcast SEO workflow",
            "repo": "AIMS",
            "workflow_preset": "podcast_episode_review",
            "policy_profile": "review_required",
            "approval_state": "pending_review",
            "status": "preview_saved",
            "created_at": "2026-09-16T20:00:00+00:00",
            "simulation": {
                "risk_summary": {"highest_risk": "medium"},
                "estimated_cost": {"cost_class": "low"},
            },
        },
        "created_at": "2026-09-16T20:00:00+00:00",
        "updated_at": "2026-09-16T20:00:00+00:00",
    }

    created = reviews.create_execution_review_plan(
        settings=_SettingsStub(),
        task="review podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        source_preview_id="execution-preview-123",
        source_simulation_id="execution-simulation-456",
        policy_profile="review_required",
        dry_run=False,
    )
    plan_id = created["plan_id"]

    listed = reviews.list_execution_review_plans(settings=_SettingsStub(), status="open")
    summary = listed["items"][0]
    assert summary["source_preview_id"] == "execution-preview-123"
    assert summary["source_simulation_id"] == "execution-simulation-456"
    assert summary["source_preview_verified"] is True
    assert summary["policy_profile"] == "review_required"

    evidence = reviews.execution_review_evidence_pack(settings=_SettingsStub(), plan_id=plan_id)
    pack = evidence["evidence_pack"]
    assert pack["source_preview_id"] == "execution-preview-123"
    assert pack["source_simulation_id"] == "execution-simulation-456"
    assert pack["source_preview_verified"] is True
    assert pack["source_preview_summary"]["risk_summary"]["highest_risk"] == "medium"
    assert pack["policy_profile"] == "review_required"


def test_execution_review_rejects_preview_provenance_mismatch(monkeypatch):
    _FakeD1.store = {
        "execution-preview-123": {
            "id": "execution-preview-123",
            "lane": "execution_previews",
            "source_type": "execution_preview",
            "source_id": "execution-preview-123",
            "title": "Execution preview",
            "url": None,
            "metadata": {
                "preview_id": "execution-preview-123",
                "simulation_id": "execution-simulation-456",
                "task": "canonical task",
                "repo": "HIVE",
                "workflow_preset": None,
                "policy_profile": "review_required",
            },
            "created_at": "2026-09-16T20:00:00+00:00",
            "updated_at": "2026-09-16T20:00:00+00:00",
        }
    }
    monkeypatch.setattr(reviews, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(reviews, "shared_execution_plan", _fake_plan)

    result = reviews.create_execution_review_plan(
        settings=_SettingsStub(),
        task="different task",
        repo="HIVE",
        source_preview_id="execution-preview-123",
        source_simulation_id="execution-simulation-456",
        policy_profile="review_required",
        dry_run=False,
    )

    assert result["ok"] is False
    assert result["error_code"] == "source_preview_mismatch"
    assert result["mismatch_fields"] == ["task"]


def test_execution_review_requires_preview_for_simulation_provenance(monkeypatch):
    _FakeD1.store = {}
    monkeypatch.setattr(reviews, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(reviews, "shared_execution_plan", _fake_plan)

    result = reviews.create_execution_review_plan(
        settings=_SettingsStub(),
        task="review podcast SEO workflow",
        source_simulation_id="execution-simulation-456",
        dry_run=False,
    )

    assert result["ok"] is False
    assert result["error_code"] == "source_preview_required"


def test_execution_review_rejects_non_preview_source_record(monkeypatch):
    _FakeD1.store = {
        "execution-preview-123": {
            "id": "execution-preview-123",
            "lane": "execution_previews",
            "source_type": "not_an_execution_preview",
            "source_id": "execution-preview-123",
            "title": "Unexpected record",
            "url": None,
            "metadata": {
                "preview_id": "execution-preview-123",
                "simulation_id": "execution-simulation-456",
                "task": "review podcast SEO workflow",
            },
            "created_at": "2026-09-16T20:00:00+00:00",
            "updated_at": "2026-09-16T20:00:00+00:00",
        }
    }
    monkeypatch.setattr(reviews, "D1MetadataStore", _FakeD1)
    monkeypatch.setattr(reviews, "shared_execution_plan", _fake_plan)

    result = reviews.create_execution_review_plan(
        settings=_SettingsStub(),
        task="review podcast SEO workflow",
        source_preview_id="execution-preview-123",
        source_simulation_id="execution-simulation-456",
        dry_run=False,
    )

    assert result["ok"] is False
    assert result["error_code"] == "source_preview_not_found"
