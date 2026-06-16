from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.repo_health import build_repo_health_report, clear_repo_health_cache


@pytest.mark.asyncio
async def test_repo_health_reports_all_governed_repositories() -> None:
    clear_repo_health_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/readiness":
            assert request.headers.get("authorization") == "Bearer rams-secret"
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/ops/health":
            return httpx.Response(200, json={"readiness": "ready"})
        if request.url.path == "/status":
            return httpx.Response(200, json={"status": "ok", "running": False})
        return httpx.Response(200, json={"ok": True, "status": "ok"})

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="https://ui.example/",
        aims_health_url="https://aims.example/health",
        aims_operational_health_url="https://aims.example/ops/health",
        rams_health_url="https://rams.example/health",
        rams_readiness_url="https://rams.example/readiness",
        rams_health_bearer_token="rams-secret",
        mast_health_url="https://mast.example/health",
        mast_status_url="https://mast.example/status",
        irs_health_url="https://images.example/",
        website_health_url="https://website.example/",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    assert report["ok"] is True
    assert report["overall_status"] == "healthy"
    assert report["summary"] == {
        "total": 7,
        "healthy": 7,
        "degraded": 0,
        "down": 0,
        "not_configured": 0,
    }
    assert [item["repo"] for item in report["repos"]] == [
        "HIVE",
        "HIVE-UI",
        "AIMS",
        "RAMS",
        "MAST",
        "IRS",
        "Website",
    ]
    assert next(item for item in report["repos"] if item["repo"] == "RAMS")["operational"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_repo_health_distinguishes_down_degraded_and_not_configured() -> None:
    clear_repo_health_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "aims.example" and request.url.path == "/ops/health":
            return httpx.Response(503, json={"readiness": "degraded"})
        if request.url.host == "rams.example":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="",
        aims_health_url="https://aims.example/health",
        aims_operational_health_url="https://aims.example/ops/health",
        rams_health_url="https://rams.example/health",
        rams_readiness_url="",
        mast_health_url="",
        mast_status_url="",
        irs_health_url="https://images.example/",
        website_health_url="https://website.example/",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    by_repo = {item["repo"]: item for item in report["repos"]}
    assert by_repo["AIMS"]["status"] == "degraded"
    assert by_repo["RAMS"]["status"] == "down"
    assert by_repo["HIVE-UI"]["status"] == "not_configured"
    assert by_repo["MAST"]["status"] == "not_configured"
    assert report["overall_status"] == "down"


@pytest.mark.asyncio
async def test_repo_health_can_be_disabled() -> None:
    clear_repo_health_cache()
    settings = Settings(app_env="test", repo_health_enabled=False)
    report = await build_repo_health_report(settings, force_refresh=True)
    assert report["overall_status"] == "disabled"
    assert report["repos"] == []
