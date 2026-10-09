"""OpenRouter benchmark feed must fail closed on malformed upstream data."""
from __future__ import annotations

import httpx
import pytest

from app.services.providers.openrouter_provider import OpenRouterProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {},
    {"data": None},
    {"data": {}},
    {"data": ["invalid"]},
])
async def test_invalid_benchmark_feed_raises(monkeypatch, payload):
    class Settings:
        openrouter_api_key = "test-key"
        openrouter_base_url = "https://openrouter.ai/api/v1"
        ai_council_benchmark_timeout_seconds = 10

    def handler(request):
        assert request.url.path.endswith("/benchmarks")
        return httpx.Response(200, json=payload)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.providers.openrouter_provider.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    # Construct the adapter without triggering its unrelated model-list client.
    provider = object.__new__(OpenRouterProvider)
    provider.settings = Settings()
    with pytest.raises(ValueError, match="benchmark"):
        await provider.list_benchmarks()


@pytest.mark.asyncio
async def test_valid_benchmark_feed_is_preserved(monkeypatch):
    class Settings:
        openrouter_api_key = "test-key"
        openrouter_base_url = "https://openrouter.ai/api/v1"
        ai_council_benchmark_timeout_seconds = 10

    expected = [{"source": "artificial-analysis", "model_permaslug": "example/model"}]
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.services.providers.openrouter_provider.httpx.AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": expected})),
            **kwargs,
        ),
    )
    provider = object.__new__(OpenRouterProvider)
    provider.settings = Settings()
    assert await provider.list_benchmarks() == expected
