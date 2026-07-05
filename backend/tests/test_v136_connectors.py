from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.connectors import (
    ai_search_connector,
    github_connector,
    openrouter_connector,
    r2_connector,
)


@pytest.mark.asyncio
async def test_openrouter_connector_reports_unconfigured_without_network():
    settings = Settings(openrouter_api_key="")
    report = await openrouter_connector.report(settings)
    assert report.configured is False
    assert report.healthy is False


@pytest.mark.asyncio
async def test_r2_connector_reports_unconfigured_without_network():
    settings = Settings()
    report = await r2_connector.report(settings)
    assert report.configured is False


@pytest.mark.asyncio
async def test_ai_search_connector_reports_unconfigured_without_network():
    settings = Settings(ai_search_enabled=False)
    report = await ai_search_connector.report(settings)
    assert report.configured is False


@pytest.mark.asyncio
async def test_github_connector_reports_unconfigured_without_token():
    settings = Settings(github_token="")
    report = await github_connector.report(settings)
    assert report.configured is False
    assert report.error is None


@pytest.mark.asyncio
async def test_github_connector_reports_rate_limit_via_mock_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer gh-token"
        if request.url.path == "/rate_limit":
            return httpx.Response(200, json={"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 123}}})
        return httpx.Response(404, json={})

    settings = Settings(github_token="gh-token")
    report = await github_connector.report(settings, transport=httpx.MockTransport(handler))

    assert report.configured is True
    assert report.healthy is True
    assert report.rate_limit["remaining"] == 4999


@pytest.mark.asyncio
async def test_github_connector_handles_upstream_error_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    settings = Settings(github_token="gh-token")
    report = await github_connector.report(settings, transport=httpx.MockTransport(handler))

    assert report.healthy is False
    assert report.error is not None
