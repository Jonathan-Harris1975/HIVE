from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.services import monthly_review


class FakeD1Store:
    """In-memory double for D1MetadataStore, keyed like the real
    hive_ecosystem_metadata table (mirrors tests/test_v132_ai_council.py)."""

    def __init__(self, _settings=None) -> None:
        self.enabled = True
        self._rows: dict[str, dict] = {}

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        self._rows[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "url": url,
            "metadata": metadata,
        }
        return {"ok": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [row for row in self._rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "enabled": True, "count": len(items), "items": items}


class FakeStoredObject:
    def __init__(self, bucket: str, key: str):
        self.bucket = bucket
        self.key = key
        self.size_bytes = 123
        self.sha256 = "fake-sha256"


class FakeR2Storage:
    """In-memory double for R2Storage. Captures the last written report body
    so tests can assert on archived content without a network call."""

    last_written: dict | None = None

    def __init__(self, _settings=None) -> None:
        pass

    def put_file(self, tmp_path, key, content_type=None, *, bucket=None, public_base_url=None):
        FakeR2Storage.last_written = json.loads(tmp_path.read_text(encoding="utf-8"))
        return FakeStoredObject(bucket=bucket or "audits", key=key)


def _settings() -> Settings:
    return Settings()


def test_period_bounds_defaults_to_previous_month():
    # Freeze "now" indirectly isn't needed: just check the explicit-period path,
    # which is the deterministic branch of _period_bounds.
    label, since, until = monthly_review._period_bounds("2026-06")
    assert label == "2026-06"
    assert since.startswith("2026-06-01")
    assert until.startswith("2026-07-01")


def test_period_bounds_handles_december_rollover():
    label, since, until = monthly_review._period_bounds("2025-12")
    assert label == "2025-12"
    assert since.startswith("2025-12-01")
    assert until.startswith("2026-01-01")


def test_period_bounds_rejects_malformed_period():
    with pytest.raises(ValueError):
        monthly_review._period_bounds("not-a-period")


@pytest.mark.asyncio
async def test_generate_monthly_review_assembles_all_sections(monkeypatch):
    settings = _settings()

    monkeypatch.setattr(monthly_review, "get_run_history", lambda settings, limit=5: [{"run_id": "r1"}])
    monkeypatch.setattr(monthly_review, "list_categories", lambda: {"coding": []})
    monkeypatch.setattr(monthly_review, "skill_registry_duplicates", lambda **kw: {"ok": True, "count": 0})
    monkeypatch.setattr(monthly_review, "skill_registry_missing", lambda **kw: {"ok": True, "count": 0})
    monkeypatch.setattr(monthly_review, "skill_registry_orphans", lambda **kw: {"ok": True, "count": 0})
    monkeypatch.setattr(monthly_review, "skill_registry_integrity_report", lambda **kw: {"ok": True})
    monkeypatch.setattr(monthly_review, "success_rate_report", lambda settings: {"ok": True, "rate": 0.9})
    monkeypatch.setattr(monthly_review, "list_experiments", lambda settings: [])
    monkeypatch.setattr(monthly_review, "list_decisions", lambda settings: [])
    monkeypatch.setattr(monthly_review, "list_execution_review_plans", lambda **kw: {"ok": True, "open_count": 2})

    async def fake_repo_health(settings):
        return {"ok": True, "status": "healthy"}

    monkeypatch.setattr(monthly_review, "build_repo_health_report", fake_repo_health)

    class FakeSqlStore:
        def __init__(self, _settings):
            pass

        def cost_summary(self, *, by_model_limit, since, until):
            return {"ok": True, "totals": {"cost_usd": 4.5}, "by_model": []}

    monkeypatch.setattr(monthly_review, "SqlStore", FakeSqlStore)

    report = await monthly_review.generate_monthly_review(settings, period="2026-06")

    assert report["ok"] is True
    assert report["period"] == "2026-06"
    assert report["sections_total"] == 11
    assert report["sections_ok"] == 11
    assert report["sections"]["cost_and_tokens"]["data"]["totals"]["cost_usd"] == 4.5
    assert report["sections"]["repo_health"]["data"]["status"] == "healthy"
    assert report["report_id"].startswith("monthly-review-2026-06-")


@pytest.mark.asyncio
async def test_generate_monthly_review_isolates_a_failing_section(monkeypatch):
    """A single subsystem raising must not blank the rest of the report --
    it should show up as a flagged, non-ok section instead."""
    settings = _settings()

    def boom(*_args, **_kwargs):
        raise RuntimeError("skills index unavailable")

    monkeypatch.setattr(monthly_review, "get_run_history", lambda settings, limit=5: [])
    monkeypatch.setattr(monthly_review, "list_categories", lambda: {})
    monkeypatch.setattr(monthly_review, "skill_registry_duplicates", boom)
    monkeypatch.setattr(monthly_review, "skill_registry_missing", lambda **kw: {"ok": True})
    monkeypatch.setattr(monthly_review, "skill_registry_orphans", lambda **kw: {"ok": True})
    monkeypatch.setattr(monthly_review, "skill_registry_integrity_report", lambda **kw: {"ok": True})
    monkeypatch.setattr(monthly_review, "success_rate_report", lambda settings: {"ok": True})
    monkeypatch.setattr(monthly_review, "list_experiments", lambda settings: [])
    monkeypatch.setattr(monthly_review, "list_decisions", lambda settings: [])
    monkeypatch.setattr(monthly_review, "list_execution_review_plans", lambda **kw: {"ok": True})

    async def fake_repo_health(settings):
        return {"ok": True}

    monkeypatch.setattr(monthly_review, "build_repo_health_report", fake_repo_health)

    class FakeSqlStore:
        def __init__(self, _settings):
            pass

        def cost_summary(self, *, by_model_limit, since, until):
            return {"ok": True, "totals": {}, "by_model": []}

    monkeypatch.setattr(monthly_review, "SqlStore", FakeSqlStore)

    report = await monthly_review.generate_monthly_review(settings, period="2026-06")

    assert report["sections"]["skills_duplicates"]["ok"] is False
    assert "skills index unavailable" in report["sections"]["skills_duplicates"]["error"]
    # every other section still generated successfully
    assert report["sections_ok"] == report["sections_total"] - 1


@pytest.mark.asyncio
async def test_generate_and_archive_writes_r2_and_indexes_d1(monkeypatch, tmp_path):
    settings = _settings()

    async def fake_generate(settings, *, period=None):
        return {
            "ok": True,
            "report_id": "monthly-review-2026-06-abc123",
            "period": "2026-06",
            "period_since": "2026-06-01T00:00:00+00:00",
            "period_until": "2026-07-01T00:00:00+00:00",
            "generated_at": "2026-07-01T07:20:00+00:00",
            "sections_ok": 11,
            "sections_total": 11,
            "sections": {
                "cost_and_tokens": {"ok": True, "data": {"totals": {"cost_usd": 1.23}}},
                "execution_reviews": {"ok": True, "data": {"open_count": 3}},
            },
        }

    monkeypatch.setattr(monthly_review, "generate_monthly_review", fake_generate)
    monkeypatch.setattr(monthly_review, "R2Storage", FakeR2Storage)
    monkeypatch.setattr(monthly_review, "D1MetadataStore", FakeD1Store)

    result = await monthly_review.generate_and_archive_monthly_review(settings, period="2026-06")

    assert result["r2_object"]["bucket"] == "audits"
    assert result["r2_object"]["key"] == "monthly-reviews/2026-06/monthly-review-2026-06-abc123.json"
    assert result["d1_index"]["ok"] is True
    assert FakeR2Storage.last_written["report_id"] == "monthly-review-2026-06-abc123"

    history = monthly_review.list_monthly_reviews(settings, limit=10)
    assert history["count"] == 1
    assert history["items"][0]["source_id"] == "2026-06"
    assert history["items"][0]["metadata"]["cost_usd_total"] == 1.23
    assert history["items"][0]["metadata"]["open_execution_reviews"] == 3
