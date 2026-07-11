from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.storage.ai_search import AiSearchClient


def _settings(**overrides: object) -> Settings:
    defaults = dict(
        ai_search_enabled=True,
        ai_search_account_id="acct-123",
        ai_search_api_token="token-123",
        ai_search_instance="hive-repositories",
        ai_search_max_attempts=3,
        ai_search_timeout_seconds=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """Replays a fixed sequence of responses, one per request, so retry
    behaviour can be exercised deterministically without real network."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if not self._responses:
            raise AssertionError("more requests were made than scripted responses")
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_retries_on_5xx_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.storage.ai_search.asyncio.sleep", fake_sleep)

    transport = _ScriptedTransport(
        [
            httpx.Response(503, json={"success": False, "errors": [{"message": "temporarily unavailable"}]}),
            httpx.Response(200, json={"success": True, "result": {"data": []}}),
        ]
    )

    client = AiSearchClient(_settings())
    orig_client_cls = httpx.AsyncClient

    class PatchedAsyncClient(orig_client_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.storage.ai_search.httpx.AsyncClient", PatchedAsyncClient)

    result = await client.search("hello world")

    assert result["ok"] is True
    assert transport.calls == 2
    # Exactly one backoff sleep between the failed attempt and the success.
    assert len(sleeps) == 1
    assert sleeps[0] > 0


@pytest.mark.asyncio
async def test_honours_retry_after_header_on_429(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.storage.ai_search.asyncio.sleep", fake_sleep)

    transport = _ScriptedTransport(
        [
            httpx.Response(429, headers={"Retry-After": "2"}, json={"success": False}),
            httpx.Response(200, json={"success": True, "result": {"data": []}}),
        ]
    )

    orig_client_cls = httpx.AsyncClient

    class PatchedAsyncClient(orig_client_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.storage.ai_search.httpx.AsyncClient", PatchedAsyncClient)

    client = AiSearchClient(_settings())
    result = await client.search("hello world")

    assert result["ok"] is True
    assert sleeps == [2.0]


@pytest.mark.asyncio
async def test_does_not_retry_non_retryable_client_error(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.storage.ai_search.asyncio.sleep", fake_sleep)

    transport = _ScriptedTransport(
        [httpx.Response(401, json={"success": False, "errors": [{"message": "invalid token"}]})]
    )

    orig_client_cls = httpx.AsyncClient

    class PatchedAsyncClient(orig_client_cls):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.storage.ai_search.httpx.AsyncClient", PatchedAsyncClient)

    client = AiSearchClient(_settings(ai_search_max_attempts=5))
    result = await client.search("hello world")

    assert result["ok"] is False
    assert result["status_code"] == 401
    # Only one request should have been made - retries would never fix a 401.
    assert transport.calls == 1
    assert sleeps == []
