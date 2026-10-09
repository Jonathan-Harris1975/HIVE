"""Worker failure-path regression tests: no real D1 or downstream writes."""
from __future__ import annotations

from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest

from app import monthly_governance_worker as worker


@pytest.mark.asyncio
async def test_duplicate_claim_never_runs_governance(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "check_readiness", lambda settings: [])
    monkeypatch.setattr(worker, "D1MetadataStore", lambda settings: object())
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: {"ok": True, "claimed": False})
    monkeypatch.setattr(worker, "get_job", lambda *a, **kw: {"ok": True, "found": True})
    generate = AsyncMock()
    monkeypatch.setattr(worker, "generate_and_archive_monthly_review", generate)
    assert await worker.execute("2026-09", owner="worker-one") == 3
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_d1_claim_failure_never_runs_governance(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "check_readiness", lambda settings: [])
    monkeypatch.setattr(worker, "D1MetadataStore", lambda settings: object())
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: {"ok": False, "error": "D1 unavailable"})
    generate = AsyncMock()
    monkeypatch.setattr(worker, "generate_and_archive_monthly_review", generate)
    assert await worker.execute("2026-09", owner="worker-one") == 2
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_run_marks_completed(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "check_readiness", lambda settings: [])
    monkeypatch.setattr(worker, "D1MetadataStore", lambda settings: object())
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: {
        "ok": True, "claimed": True, "job_id": "monthly-governance:2026-09"
    })
    monkeypatch.setattr(worker, "generate_and_archive_monthly_review", AsyncMock(
        return_value={"ok": True, "report_id": "report-1", "sections_ok": 3, "sections_total": 3}
    ))
    transitions = []
    def complete(*a, **kw):
        transitions.append(kw)
        return {"ok": True, "completed": True}
    monkeypatch.setattr(worker, "complete_job", complete)
    assert await worker.execute("2026-09", owner="worker-one") == 0
    assert transitions[0]["owner"] == "worker-one"
    assert transitions[0]["succeeded"] is True


@pytest.mark.asyncio
async def test_crash_keeps_claim_for_manual_reconciliation(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: object())
    monkeypatch.setattr(worker, "check_readiness", lambda settings: [])
    monkeypatch.setattr(worker, "D1MetadataStore", lambda settings: object())
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: {
        "ok": True, "claimed": True, "job_id": "monthly-governance:2026-09"
    })
    monkeypatch.setattr(worker, "generate_and_archive_monthly_review", AsyncMock(
        side_effect=RuntimeError("unknown partial write")
    ))
    completed = []
    monkeypatch.setattr(worker, "complete_job", lambda *a, **kw: completed.append(kw))
    with pytest.raises(RuntimeError, match="unknown partial write"):
        await worker.execute("2026-09", owner="worker-one")
    assert completed == []


def test_readiness_lists_missing_config_without_values():
    settings = SimpleNamespace(d1_enabled=False, d1_account_id="", d1_database_id="", d1_api_key="")
    missing = worker.check_readiness(settings)
    assert any("D1_ENABLED" in item for item in missing)
    assert "AIMS_API_KEY" in missing
    assert "RAMS_API_KEY" in missing


@pytest.mark.asyncio
async def test_preflight_does_not_claim_or_execute(monkeypatch):
    settings = SimpleNamespace(
        d1_enabled=True, d1_account_id="account", d1_database_id="database",
        d1_api_key="secret", cf_r2_account_id="account",
        cf_r2_access_key_id="access", cf_r2_secret_access_key="secret",
        r2_bucket_audits="audits", aims_base_url="https://aims.test",
        aims_api_key="secret", rams_base_url="https://rams.test", rams_api_key="secret",
    )
    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    class Store:
        enabled = True
        def diagnostics(self):
            return {"ok": True, "schema_ready": True}
    monkeypatch.setattr(worker, "D1MetadataStore", lambda value: Store())
    claim = []
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: claim.append(kw))
    generate = AsyncMock()
    monkeypatch.setattr(worker, "generate_and_archive_monthly_review", generate)
    assert await worker.execute("2026-09", preflight_only=True) == 0
    assert claim == []
    generate.assert_not_awaited()
