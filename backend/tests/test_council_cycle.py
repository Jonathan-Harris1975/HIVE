from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import council_cycle
from app.services.ai_council import CouncilRunReport


def _report(run_id: str = "council-test") -> CouncilRunReport:
    return CouncilRunReport(
        run_id=run_id,
        occurred_at="2026-09-05T03:00:00+00:00",
        providers_discovered=1,
        models_seen=10,
        new_models=[],
        retired_models=[],
        promotions=[],
        weights_used={},
    )


@pytest.mark.asyncio
async def test_cycle_fails_closed_and_skips_downstream_for_empty_registry(monkeypatch):
    settings = Settings(model_registry_min_visible_score=0.72)
    completions: list[dict[str, object]] = []

    async def fake_run(_settings):
        return _report()

    async def forbidden_sync(*args, **kwargs):
        raise AssertionError("empty registry must never be pushed downstream")

    def fake_completion(_settings, **kwargs):
        completions.append(kwargs)
        return True

    monkeypatch.setattr(council_cycle, "run_council", fake_run)
    monkeypatch.setattr(council_cycle, "list_categories", lambda: {"coding": []})
    monkeypatch.setattr(council_cycle, "sync_model_registry_downstream", forbidden_sync)
    monkeypatch.setattr(council_cycle, "record_run_completion", fake_completion)

    result = await council_cycle.execute_council_cycle(settings)

    assert result["ok"] is False
    assert result["failure_stage"] == "model_registry"
    assert result["qualified_model_count"] == 0
    assert result["downstream_sync"]["skipped"] is True
    assert completions[0]["completion_status"] == "degraded"


@pytest.mark.asyncio
async def test_cycle_syncs_when_registry_has_qualified_model(monkeypatch):
    settings = Settings(model_registry_min_visible_score=0.72)
    synced: list[dict[str, object]] = []

    async def fake_run(_settings):
        return _report()

    async def fake_sync(_settings, **kwargs):
        synced.append(kwargs)
        return {"ok": True, "enabled": True, "targets": {}}

    monkeypatch.setattr(council_cycle, "run_council", fake_run)
    monkeypatch.setattr(
        council_cycle,
        "list_categories",
        lambda: {"coding": [{"model_id": "acme/coder", "score": 0.91}]},
    )
    monkeypatch.setattr(council_cycle, "sync_model_registry_downstream", fake_sync)
    monkeypatch.setattr(council_cycle, "record_run_completion", lambda *args, **kwargs: True)

    result = await council_cycle.execute_council_cycle(settings)

    assert result["ok"] is True
    assert result["qualified_model_count"] == 1
    assert synced[0]["source_run_id"] == "council-test"


@pytest.mark.asyncio
async def test_cycle_reuses_verified_fresh_run(monkeypatch):
    settings = Settings()
    existing = {
        "run_id": "existing",
        "completion_status": "completed",
        "downstream_sync": {"ok": True},
    }

    monkeypatch.setattr(council_cycle, "latest_verified_run", lambda settings, since=None: existing)

    async def forbidden_run(_settings):
        raise AssertionError("fresh completed monthly Council should be reused")

    monkeypatch.setattr(council_cycle, "run_council", forbidden_run)

    result = await council_cycle.execute_council_cycle(
        settings,
        reuse_since=council_cycle.datetime(2026, 9, 1, tzinfo=council_cycle.UTC),
    )

    assert result["ok"] is True
    assert result["reused"] is True
    assert result["run"]["run_id"] == "existing"


def test_monthly_governance_status_requires_current_month_verified_sync(monkeypatch):
    settings = Settings()
    now = council_cycle.datetime(2026, 10, 8, 12, 0, tzinfo=council_cycle.UTC)

    stale = {
        "run_id": "sep-run",
        "occurred_at": "2026-09-30T23:00:00+00:00",
        "completed_at": "2026-09-30T23:05:00+00:00",
        "completion_status": "completed",
        "downstream_sync": {"ok": True},
    }
    monkeypatch.setattr(council_cycle, "get_run_history", lambda settings, limit=50: [stale])

    stale_status = council_cycle.monthly_governance_status(settings, now=now)

    assert stale_status["ok"] is False
    assert stale_status["fresh"] is False
    assert stale_status["period"] == "2026-10"
    assert "current monthly governance period" in stale_status["reason"]

    verified = {
        "run_id": "oct-run",
        "occurred_at": "2026-10-01T07:00:00+00:00",
        "completed_at": "2026-10-01T07:04:00+00:00",
        "completion_status": "completed",
        "downstream_sync": {"ok": True},
    }
    monkeypatch.setattr(council_cycle, "get_run_history", lambda settings, limit=50: [stale, verified])

    fresh_status = council_cycle.monthly_governance_status(settings, now=now)

    assert fresh_status["ok"] is True
    assert fresh_status["fresh"] is True
    assert fresh_status["latest_verified_run_id"] == "oct-run"
    assert fresh_status["downstream_sync_ok"] is True


def test_monthly_governance_status_exposes_failed_downstream_sync(monkeypatch):
    settings = Settings()
    now = council_cycle.datetime(2026, 10, 8, 12, 0, tzinfo=council_cycle.UTC)
    failed = {
        "run_id": "oct-failed",
        "occurred_at": "2026-10-01T07:00:00+00:00",
        "completed_at": "2026-10-01T07:02:00+00:00",
        "completion_status": "degraded",
        "downstream_sync": {"ok": False},
    }
    monkeypatch.setattr(council_cycle, "get_run_history", lambda settings, limit=50: [failed])

    status = council_cycle.monthly_governance_status(settings, now=now)

    assert status["ok"] is False
    assert status["completion_status"] == "degraded"
    assert status["downstream_sync_ok"] is False
