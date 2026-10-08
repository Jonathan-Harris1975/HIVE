"""Worker failure-path regression tests: no real D1 or downstream writes."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app import monthly_governance_worker as worker


@pytest.mark.asyncio
async def test_duplicate_claim_never_runs_governance(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: object())
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
    monkeypatch.setattr(worker, "D1MetadataStore", lambda settings: object())
    monkeypatch.setattr(worker, "claim_job", lambda *a, **kw: {"ok": False, "error": "D1 unavailable"})
    generate = AsyncMock()
    monkeypatch.setattr(worker, "generate_and_archive_monthly_review", generate)
    assert await worker.execute("2026-09", owner="worker-one") == 2
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_run_marks_completed(monkeypatch):
    monkeypatch.setattr(worker, "get_settings", lambda: object())
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
