from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api import monthly_review as api
from app.core.config import Settings


@pytest.mark.asyncio
async def test_generate_monthly_review_accepts_and_schedules_one_background_task(monkeypatch):
    monkeypatch.setattr(api, "D1MetadataStore", lambda _settings: object())
    monkeypatch.setattr(
        api,
        "claim_job",
        lambda _store, *, period, owner: {
            "ok": True, "claimed": True, "job_id": f"monthly-governance:{period}"
        },
    )
    tasks = BackgroundTasks()

    accepted = await api.generate_monthly_review_endpoint(
        tasks, period="2026-09", settings=Settings()
    )

    assert accepted == {
        "ok": True,
        "accepted": True,
        "period": "2026-09",
        "job_id": "monthly-governance:2026-09",
        "status": "claimed",
        "status_url": "/v1/monthly-review/jobs/2026-09",
    }
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].func is api._run_monthly_review_job
    assert tasks.tasks[0].args[1] == "2026-09"
    assert tasks.tasks[0].args[2].startswith("api-")


@pytest.mark.asyncio
async def test_generate_monthly_review_rejects_duplicate_claim(monkeypatch):
    monkeypatch.setattr(api, "D1MetadataStore", lambda _settings: object())
    monkeypatch.setattr(
        api,
        "claim_job",
        lambda _store, *, period, owner: {
            "ok": True, "claimed": False, "job_id": f"monthly-governance:{period}"
        },
    )
    monkeypatch.setattr(
        api,
        "get_job",
        lambda _store, *, period: {"ok": True, "found": True, "period": period},
    )

    with pytest.raises(HTTPException) as error:
        await api.generate_monthly_review_endpoint(
            BackgroundTasks(), period="2026-09", settings=Settings()
        )

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_generate_monthly_review_rejects_current_month_before_claim(monkeypatch):
    current_month = api.datetime.now(api.UTC).strftime("%Y-%m")

    def unexpected_claim(*_args, **_kwargs):
        raise AssertionError("current month must not be claimed")

    monkeypatch.setattr(api, "claim_job", unexpected_claim)

    with pytest.raises(HTTPException) as error:
        await api.generate_monthly_review_endpoint(
            BackgroundTasks(), period=current_month, settings=Settings()
        )

    assert error.value.status_code == 400
