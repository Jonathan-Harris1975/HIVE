from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.openrouter import OpenRouterClient


@pytest.mark.asyncio
async def test_payload_attempts_skip_invalid_model_and_use_free_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_free_fallback_model="free-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)

    async def fake_model_ids() -> set[str]:
        return {"free-good:free"}

    monkeypatch.setattr(client, "model_ids", fake_model_ids)
    attempts = [
        item async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "dead-model", "messages": []},
            ["free-good:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["free-good:free"]


@pytest.mark.asyncio
async def test_payload_attempts_fails_open_when_model_preflight_is_disabled() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_model_preflight_enabled=False,
        openrouter_free_fallback_model="free-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)

    attempts = [
        item async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "dead-model", "messages": []},
            ["free-good:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["dead-model", "free-good:free"]


@pytest.mark.asyncio
async def test_payload_attempts_respects_max_fallback_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_max_fallback_attempts=1,
    )
    client = OpenRouterClient(settings)

    async def fake_model_ids() -> set[str]:
        return {"primary", "fallback-one:free", "fallback-two:free"}

    monkeypatch.setattr(client, "model_ids", fake_model_ids)
    attempts = [
        item async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "primary", "messages": []},
            ["fallback-one:free", "fallback-two:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["primary", "fallback-one:free"]

@pytest.mark.asyncio
async def test_chat_completion_does_not_call_invalid_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_free_fallback_model="free-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)
    called_models: list[str] = []

    async def fake_model_ids() -> set[str]:
        return {"free-good:free"}

    async def fake_post_json(payload: dict[str, object]) -> dict[str, object]:
        called_models.append(str(payload["model"]))
        return {"model": payload["model"], "choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(client, "model_ids", fake_model_ids)
    monkeypatch.setattr(client, "_post_json", fake_post_json)

    response = await client.chat_completion(
        {"model": "dead-model", "messages": []},
        fallback_models=["free-good:free"],
    )

    assert response["model"] == "free-good:free"
    assert called_models == ["free-good:free"]


@pytest.mark.asyncio
async def test_preflight_does_not_filter_controlled_fallback_aliases(monkeypatch):
    settings = Settings(
        openrouter_api_key="test",
        openrouter_model_preflight_enabled=True,
        openrouter_free_fallback_model="router/free-alias:free",
    )
    client = OpenRouterClient(settings)

    async def fake_model_ids():
        return {"router/free-alias-20260604:free"}

    monkeypatch.setattr(client, "model_ids", fake_model_ids)
    payload = {"model": "dead/model", "messages": []}

    attempts = [item async for item in client._payload_attempts(payload, ["router/free-alias:free"])]

    assert attempts == [{"model": "router/free-alias:free", "messages": []}]


@pytest.mark.asyncio
async def test_preflight_keeps_free_fallback_when_all_candidates_would_be_filtered(monkeypatch):
    settings = Settings(
        openrouter_api_key="test",
        openrouter_model_preflight_enabled=True,
        openrouter_free_fallback_model="nvidia/nemotron-3-ultra-550b-a55b:free",
    )
    client = OpenRouterClient(settings)

    async def fake_model_ids():
        return set()

    monkeypatch.setattr(client, "model_ids", fake_model_ids)
    payload = {"model": "dead/model", "messages": []}

    attempts = [item async for item in client._payload_attempts(payload, [])]

    assert attempts == [{"model": "nvidia/nemotron-3-ultra-550b-a55b:free", "messages": []}]
