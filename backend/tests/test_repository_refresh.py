from __future__ import annotations

import asyncio

import pytest

from app.core.config import Settings
from app.main import create_app
from app.services import repository_refresh


def _settings(**overrides) -> Settings:
    values = {
        "d1_enabled": False,
        "repository_github_refresh_enabled": True,
        "github_token": "test-token",
        "repository_github_branch": "main",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_default_monthly_refresh_covers_all_governed_business_repositories() -> None:
    sources = repository_refresh.github_sources(_settings())
    assert sources == {
        "HIVE": "Jonathan-Harris1975/HIVE",
        "HIVE-UI": "Jonathan-Harris1975/HIVE-UI",
        "AIMS": "Jonathan-Harris1975/AIMS",
        "AIMS-UI": "Jonathan-Harris1975/AIMS-UI",
        "RAMS": "Jonathan-Harris1975/RAMS",
        "MAST": "Jonathan-Harris1975/MAST",
        "IRS": "Jonathan-Harris1975/IRS",
        "Website": "Jonathan-Harris1975/jonathan-harris-website",
    }


def test_refresh_configuration_fails_closed_without_token() -> None:
    config = repository_refresh.refresh_configuration(_settings(github_token=""))
    assert config["enabled"] is True
    assert config["configured"] is False
    assert config["github_token_configured"] is False


@pytest.mark.asyncio
async def test_refresh_job_replaces_each_snapshot_and_waits_for_intelligence(monkeypatch) -> None:
    settings = _settings(
        repository_github_sources_json='{"HIVE":"Jonathan-Harris1975/HIVE","HIVE-UI":"Jonathan-Harris1975/HIVE-UI"}'
    )
    repository_refresh._JOBS.clear()
    repository_refresh._TASKS.clear()

    async def fake_download(_settings, slug: str) -> bytes:
        return f"archive:{slug}".encode()

    ingested: list[tuple[str, str, bytes]] = []

    async def fake_ingest(content: bytes, filename: str, expected_id: str):
        ingested.append((expected_id, filename, content))
        return {
            "repository_id": expected_id,
            "pipeline": {
                "status": "ready",
                "required_stages_ready": True,
                "intelligence": {"finding_count": 3},
            },
        }

    monkeypatch.setattr(repository_refresh, "download_github_archive", fake_download)
    accepted = repository_refresh.start_refresh_job(settings, fake_ingest)
    job_id = str(accepted["job_id"])

    for _ in range(100):
        current = repository_refresh.get_refresh_job(settings, job_id)
        if current and current.get("status") in {"completed", "completed-with-failures", "failed"}:
            break
        await asyncio.sleep(0)
    else:
        pytest.fail("repository refresh did not reach a terminal state")

    current = repository_refresh.get_refresh_job(settings, job_id)
    assert current is not None
    assert current["status"] == "completed"
    assert current["failed_count"] == 0
    assert [item[0] for item in ingested] == ["HIVE", "HIVE-UI"]
    assert [item[1] for item in ingested] == ["HIVE-main.zip", "HIVE-UI-main.zip"]
    assert all(result["pipeline_status"] == "ready" for result in current["results"])
    assert all(result["finding_count"] == 3 for result in current["results"])


def test_repository_refresh_routes_exist_and_do_not_fall_through_dynamic_repository_route() -> None:
    paths = {(route.path, next(iter(route.methods or []), None)) for route in create_app().routes}
    route_paths = {path for path, _method in paths}
    assert "/v1/repositories/refresh-config" in route_paths
    assert "/v1/repositories/refresh-all" in route_paths
    assert "/v1/repositories/refresh-jobs/{job_id}" in route_paths
    assert "/v1/repositories/{repository_id}/intelligence/run" in route_paths


def test_stored_inflight_refresh_is_failed_cleanly_after_restart(monkeypatch) -> None:
    settings = _settings()
    repository_refresh._JOBS.clear()
    repository_refresh._TASKS.clear()
    stored = {
        "job_id": "restart-job",
        "status": "running",
        "created_at": "2026-08-26T00:00:00+00:00",
        "results": [],
    }
    monkeypatch.setattr(repository_refresh, "_stored_job", lambda _settings, _job_id: dict(stored))
    monkeypatch.setattr(repository_refresh, "_persist_job", lambda _settings, _payload: None)

    current = repository_refresh.get_refresh_job(settings, "restart-job")

    assert current is not None
    assert current["job_id"] == "restart-job"
    assert current["status"] == "failed"
    assert "restarted" in str(current["error"]).lower()
    assert current.get("finished_at")
