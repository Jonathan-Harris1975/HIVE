from __future__ import annotations

import pytest

from app.services.monthly_governance_jobs import claim_job, get_job


class FakeD1:
    enabled = True

    def __init__(self):
        self.rows = {}

    def query(self, sql, params):
        if "INSERT INTO" in sql:
            job_id, lane, period, owner, now, _, _ = params
            if job_id in self.rows:
                return {"ok": True, "result": [{"results": []}]}
            self.rows[job_id] = {
                "id": job_id, "source_id": period,
                "metadata_json": '{"status":"claimed","owner":"' + owner + '"}',
                "created_at": now, "updated_at": now,
            }
            return {"ok": True, "result": [{"results": [{"id": job_id}]}]}
        return {"ok": True, "result": [{"results": [
            self.rows[params[0]] ] if params[0] in self.rows else []}]}


def test_claim_is_single_use_and_status_is_persistent():
    store = FakeD1()
    assert claim_job(store, period="2026-09", owner="worker-1")["claimed"] is True
    assert claim_job(store, period="2026-09", owner="worker-2")["claimed"] is False
    job = get_job(store, period="2026-09")
    assert job["found"] is True
    assert job["state"]["owner"] == "worker-1"


def test_invalid_period_never_writes():
    store = FakeD1()
    with pytest.raises(ValueError):
        claim_job(store, period="2026-13", owner="worker-1")
    assert not store.rows


def test_unavailable_d1_fails_closed():
    store = FakeD1()
    store.enabled = False
    assert claim_job(store, period="2026-09", owner="worker-1")["claimed"] is False


def test_get_job_missing_period():
    assert get_job(FakeD1(), period="2026-09") == {"ok": True, "found": False}


def test_get_job_invalid_period_raises():
    with pytest.raises(ValueError):
        get_job(FakeD1(), period="2026-13")


def test_get_job_d1_unavailable_fails_closed():
    store = FakeD1()
    store.enabled = False
    assert get_job(store, period="2026-09") == {
        "ok": False, "found": False, "error": "D1 unavailable"
    }
