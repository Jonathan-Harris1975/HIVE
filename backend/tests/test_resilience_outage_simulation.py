"""Resilience tests: force each external dependency (AIMS/RAMS/MAST-style HTTP
probes, R2, OpenRouter) to fail at the transport level and assert HIVE reports a
structured degraded/error result instead of raising or crashing the caller.

These intentionally test at the service-function level (not through the full
FastAPI app + TestClient) so each failure mode is isolated and deterministic:
transport failures are injected via httpx.MockTransport / monkeypatching the
exact client method each service calls, mirroring the existing style used in
test_production_readiness.py and test_repo_health_dashboard.py.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.dependency_readiness import (
    build_dependency_readiness_report,
    clear_dependency_readiness_cache,
)
from app.services.openrouter import OpenRouterClient
from app.services.repo_health import ProbeTarget, _probe_target
from app.storage.r2 import R2Storage


# ---------------------------------------------------------------------------
# AIMS / RAMS / MAST-style HTTP health probes (services/repo_health.py)
# ---------------------------------------------------------------------------


def _raising_transport(exc: Exception) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(_handler)


@pytest.mark.asyncio
async def test_aims_probe_reports_down_when_unreachable() -> None:
    """AIMS connection refused/unreachable must degrade to status='down', not raise."""

    target = ProbeTarget(
        repo="AIMS",
        label="AIMS",
        category="background_api",
        description="AI Management Suite background API",
        health_url="https://aims.example.invalid/livez",
    )
    transport = _raising_transport(
        httpx.ConnectError("simulated AIMS outage", request=httpx.Request("GET", target.health_url))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _probe_target(client, target)

    assert result["status"] == "down"
    assert result["liveness"]["status"] == "down"
    assert result["liveness"]["configured"] is True


@pytest.mark.asyncio
async def test_rams_probe_reports_down_on_timeout() -> None:
    """RAMS timing out must degrade to status='down', not raise or hang."""

    target = ProbeTarget(
        repo="RAMS",
        label="RAMS",
        category="background_api",
        description="Repository Automation Management Service",
        health_url="https://rams.example.invalid/livez",
        operational_url="https://rams.example.invalid/readiness",
        operational_token="test-token",
    )
    transport = _raising_transport(
        httpx.ConnectTimeout(
            "simulated RAMS timeout", request=httpx.Request("GET", target.health_url)
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await _probe_target(client, target)

    assert result["status"] == "down"
    assert result["liveness"]["status"] == "down"
    assert result["liveness"]["http_status"] is None


@pytest.mark.asyncio
async def test_mast_probe_not_configured_when_urls_missing() -> None:
    """A target with no health URL configured must report 'not_configured', not error."""

    target = ProbeTarget(
        repo="MAST",
        label="MAST",
        category="background_api",
        description="Master automation scheduler worker",
        health_url="",
    )
    async with httpx.AsyncClient(transport=_raising_transport(RuntimeError("never called"))) as client:
        result = await _probe_target(client, target)

    assert result["status"] == "not_configured"
    assert result["liveness"]["configured"] is False


# ---------------------------------------------------------------------------
# R2 (services/dependency_readiness.py)
# ---------------------------------------------------------------------------


def _production_settings_with_r2_lane(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "production",
        "APP_VERSION": "test-production",
        "ADMIN_BEARER_TOKEN": "a" * 48,
        "CORS_ORIGINS": "https://hive-ui.pages.dev",
        "ALLOWED_HOSTS": "testserver,*.koyeb.app",
        "PRODUCTION_REQUIRE_OPENROUTER": False,
        "PRODUCTION_REQUIRE_R2": False,
        "PRODUCTION_REQUIRE_DATABASE": False,
        "REPO_HEALTH_ENABLED": False,
        "READINESS_DEPENDENCY_PROBES_ENABLED": True,
        "R2_REQUIRED_READ_LANES": "uploads",
        "R2_BUCKET_UPLOADS": "test-uploads-bucket",
    }
    values.update(overrides)
    return Settings(**values)


def test_dependency_readiness_reports_error_when_r2_is_unreachable(monkeypatch) -> None:
    """A required R2 lane that raises OSError/RuntimeError must surface as a
    named 'error' probe (and flip overall readiness to not-ready) instead of
    raising out of /readyz."""

    clear_dependency_readiness_cache()

    def _raise_os_error(self: R2Storage, **kwargs: object) -> None:
        raise OSError("simulated R2 outage")

    monkeypatch.setattr(R2Storage, "list_objects_page", _raise_os_error)

    try:
        settings = _production_settings_with_r2_lane()
        report = build_dependency_readiness_report(settings, force=True)
    finally:
        clear_dependency_readiness_cache()

    assert report.probes_enabled is True
    matching = [p for p in report.probes if p.name == "r2_lane:uploads"]
    assert matching, "expected a probe result for the required 'uploads' lane"
    assert matching[0].status == "error"
    assert report.ready is False


def test_dependency_readiness_reports_ok_when_r2_is_reachable(monkeypatch) -> None:
    """Sanity check for the test above: when the probe succeeds, the lane is 'ok'
    and doesn't fail overall readiness by itself."""

    clear_dependency_readiness_cache()

    def _fake_list_objects_page(self: R2Storage, **kwargs: object) -> object:
        class _Page:
            objects: list[object] = []
            prefixes: list[str] = []
            next_cursor: str | None = None
            scanned_count = 0
            truncated = False

        return _Page()

    monkeypatch.setattr(R2Storage, "list_objects_page", _fake_list_objects_page)

    try:
        settings = _production_settings_with_r2_lane()
        report = build_dependency_readiness_report(settings, force=True)
    finally:
        clear_dependency_readiness_cache()

    matching = [p for p in report.probes if p.name == "r2_lane:uploads"]
    assert matching and matching[0].status == "ok"


# ---------------------------------------------------------------------------
# OpenRouter (services/openrouter.py)
# ---------------------------------------------------------------------------


def _settings_with_openrouter_key(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openrouter_api_key": "test-key-123",
        "openrouter_model_preflight_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.asyncio
async def test_openrouter_post_json_degrades_gracefully_on_connect_error(monkeypatch) -> None:
    """A fully unreachable OpenRouter must come back as a retryable structured
    error, never as an unhandled exception."""

    settings = _settings_with_openrouter_key()
    client = OpenRouterClient(settings)

    async def _raise_connect_error(self, url, *, headers=None, json=None, **kwargs):
        raise httpx.ConnectError("simulated OpenRouter outage", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _raise_connect_error)

    result = await client._post_json({"model": "test-model", "messages": []})

    assert result["_retryable_model_error"] is True
    assert result["status_code"] == 502


@pytest.mark.asyncio
async def test_openrouter_post_json_degrades_gracefully_on_timeout(monkeypatch) -> None:
    """A slow/hanging OpenRouter must come back as a retryable timeout, not hang
    the caller or raise."""

    settings = _settings_with_openrouter_key()
    client = OpenRouterClient(settings)

    async def _raise_timeout(self, url, *, headers=None, json=None, **kwargs):
        raise httpx.ConnectTimeout("simulated OpenRouter timeout", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _raise_timeout)

    result = await client._post_json({"model": "test-model", "messages": []})

    assert result["_retryable_model_error"] is True
    assert result["status_code"] == 408


@pytest.mark.asyncio
async def test_openrouter_chat_completion_reports_all_attempts_failed(monkeypatch) -> None:
    """End-to-end: when every attempt fails at the transport level,
    chat_completion() must return a structured failure payload rather than
    letting the exception propagate to the API layer."""

    settings = _settings_with_openrouter_key(openrouter_model_preflight_enabled=False)
    client = OpenRouterClient(settings)

    async def _raise_connect_error(self, url, *, headers=None, json=None, **kwargs):
        raise httpx.ConnectError("simulated OpenRouter outage", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _raise_connect_error)

    result = await client.chat_completion({"model": "test-model", "messages": []}, fallback_models=[])

    assert result.get("_all_attempts_failed") is True
