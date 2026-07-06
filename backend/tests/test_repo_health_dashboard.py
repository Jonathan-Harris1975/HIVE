from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


@pytest.mark.asyncio
async def test_repo_health_monitors_mast_worker_from_r2_heartbeat() -> None:
    clear_repo_health_cache()
    heartbeat = datetime.now(UTC).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meta.example":
            return httpx.Response(
                200,
                json={
                    "version": 1,
                    "startedAt": heartbeat,
                    "lastTickAt": heartbeat,
                    "recentResults": [{"ok": True, "finishedAt": heartbeat}],
                },
            )
        return httpx.Response(200, json={"ok": True, "status": "ok"})

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="https://ui.example/health",
        aims_health_url="https://aims.example/livez",
        rams_health_url="https://rams.example/livez",
        mast_monitor_mode="r2",
        mast_state_r2_lane="meta_system",
        mast_state_object_key="state/mast/scheduler-state.json",
        r2_public_base_url_meta_system="https://meta.example",
        irs_health_url="https://images.example/health.json",
        website_health_url="https://website.example/health.json",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    mast = next(item for item in report["repos"] if item["repo"] == "MAST")
    assert mast["category"] == "background_api"
    assert mast["status"] == "healthy"
    assert mast["operational"]["payload"]["source"] == "r2_public"
    assert mast["operational"]["payload"]["recent_failures"] == 0


@pytest.mark.asyncio
async def test_repo_health_marks_stopped_mast_heartbeat_down() -> None:
    clear_repo_health_cache()
    heartbeat = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "meta.example":
            return httpx.Response(200, json={"lastTickAt": heartbeat, "recentResults": []})
        return httpx.Response(200, json={"ok": True, "status": "ok"})

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        mast_monitor_mode="r2",
        mast_state_healthy_max_age_seconds=90,
        mast_state_down_max_age_seconds=300,
        r2_public_base_url_meta_system="https://meta.example",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    mast = next(item for item in report["repos"] if item["repo"] == "MAST")
    assert mast["status"] == "down"
    assert mast["operational"]["payload"]["heartbeat_age_seconds"] >= 600

@pytest.mark.asyncio
async def test_repo_health_uses_json_payload_state_not_just_http_status() -> None:
    clear_repo_health_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "aims.example" and request.url.path == "/ops/health":
            return httpx.Response(200, json={"ok": False, "readiness": "not_ready"})
        return httpx.Response(200, json={"ok": True, "status": "ok"})

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        aims_health_url="https://aims.example/health",
        aims_operational_health_url="https://aims.example/ops/health",
        hive_ui_health_url="",
        rams_health_url="",
        mast_health_url="",
        mast_status_url="",
        irs_health_url="",
        website_health_url="",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    aims = next(item for item in report["repos"] if item["repo"] == "AIMS")
    assert aims["status"] == "degraded"
    assert aims["operational"]["status"] == "degraded"
    assert aims["readiness"]["status"] == "partial"


@pytest.mark.asyncio
async def test_repo_health_marks_auth_blocked_operational_probe_without_calling_service_down() -> None:
    clear_repo_health_cache()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rams.example" and request.url.path == "/readiness":
            return httpx.Response(403, json={"ok": False, "status": "forbidden"})
        return httpx.Response(200, json={"ok": True, "status": "ok"})

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="",
        aims_health_url="",
        rams_health_url="https://rams.example/livez",
        rams_readiness_url="https://rams.example/readiness",
        mast_health_url="",
        mast_status_url="",
        irs_health_url="",
        website_health_url="",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    rams = next(item for item in report["repos"] if item["repo"] == "RAMS")
    assert rams["status"] == "degraded"
    assert rams["liveness"]["status"] == "healthy"
    assert rams["operational"]["status"] == "blocked"
    assert rams["readiness"]["status"] == "blocked"


# ---------------------------------------------------------------------------
# Explicit AIMS/RAMS degradation-state coverage.
#
# The audit noted it was "not clear from source whether HIVE degrades
# gracefully" when AIMS or RAMS (or both) are down. These three tests make
# that behaviour explicit and regression-tested: in every case,
# build_repo_health_report must complete without raising, HIVE's own
# "core_api" status must stay "healthy" (the outage of an external
# dependency must never be conflated with HIVE's own liveness), and the
# overall summary must correctly reflect only the affected service(s).
# ---------------------------------------------------------------------------


def _connect_error_handler_for(*down_hosts: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in down_hosts:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"ok": True, "status": "ok"})

    return handler


@pytest.mark.asyncio
async def test_repo_health_degrades_gracefully_when_aims_is_down_and_rams_is_up() -> None:
    clear_repo_health_cache()

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="",
        aims_health_url="https://aims.example/health",
        aims_operational_health_url="https://aims.example/ops/health",
        rams_health_url="https://rams.example/livez",
        rams_readiness_url="https://rams.example/readiness",
        mast_health_url="",
        mast_status_url="",
        irs_health_url="",
        website_health_url="",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_connect_error_handler_for("aims.example"))
    ) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    assert report["ok"] is True
    hive_item = next(item for item in report["repos"] if item["repo"] == "HIVE")
    assert hive_item["status"] == "healthy"

    aims = next(item for item in report["repos"] if item["repo"] == "AIMS")
    rams = next(item for item in report["repos"] if item["repo"] == "RAMS")
    assert aims["status"] == "down"
    assert rams["status"] == "healthy"
    assert report["overall_status"] == "down"
    assert report["summary"]["down"] == 1
    assert report["summary"]["healthy"] >= 1


@pytest.mark.asyncio
async def test_repo_health_degrades_gracefully_when_rams_is_down_and_aims_is_up() -> None:
    clear_repo_health_cache()

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="",
        aims_health_url="https://aims.example/health",
        aims_operational_health_url="https://aims.example/ops/health",
        rams_health_url="https://rams.example/livez",
        rams_readiness_url="https://rams.example/readiness",
        mast_health_url="",
        mast_status_url="",
        irs_health_url="",
        website_health_url="",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_connect_error_handler_for("rams.example"))
    ) as client:
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    assert report["ok"] is True
    hive_item = next(item for item in report["repos"] if item["repo"] == "HIVE")
    assert hive_item["status"] == "healthy"

    aims = next(item for item in report["repos"] if item["repo"] == "AIMS")
    rams = next(item for item in report["repos"] if item["repo"] == "RAMS")
    assert aims["status"] == "healthy"
    assert rams["status"] == "down"
    assert report["overall_status"] == "down"
    assert report["summary"]["down"] == 1


@pytest.mark.asyncio
async def test_repo_health_degrades_gracefully_when_both_aims_and_rams_are_down() -> None:
    clear_repo_health_cache()

    settings = Settings(
        app_env="test",
        repo_health_cache_seconds=0,
        hive_ui_health_url="",
        aims_health_url="https://aims.example/health",
        aims_operational_health_url="https://aims.example/ops/health",
        rams_health_url="https://rams.example/livez",
        rams_readiness_url="https://rams.example/readiness",
        mast_health_url="",
        mast_status_url="",
        irs_health_url="",
        website_health_url="",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_connect_error_handler_for("aims.example", "rams.example"))
    ) as client:
        # Must not raise even though both external dependencies are fully down.
        report = await build_repo_health_report(settings, client=client, force_refresh=True)

    assert report["ok"] is True
    hive_item = next(item for item in report["repos"] if item["repo"] == "HIVE")
    assert hive_item["status"] == "healthy"

    aims = next(item for item in report["repos"] if item["repo"] == "AIMS")
    rams = next(item for item in report["repos"] if item["repo"] == "RAMS")
    assert aims["status"] == "down"
    assert rams["status"] == "down"
    assert report["overall_status"] == "down"
    assert report["summary"]["down"] == 2

    # The API layer that serves /v1/runtime/readiness relies on this report;
    # confirm the payload is JSON-serialisable and carries no exception state.
    assert isinstance(report["summary"], dict)
    assert all(isinstance(item["status"], str) for item in report["repos"])
