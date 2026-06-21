from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.openrouter import OpenRouterClient


@pytest.mark.asyncio
async def test_payload_attempts_skip_invalid_model_and_use_free_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        item
        async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "dead-model", "messages": []},
            ["free-good:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["free-good:free"]


@pytest.mark.asyncio
async def test_payload_attempts_still_prefers_free_fallback_when_preflight_is_disabled() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_model_preflight_enabled=False,
        openrouter_free_fallback_model="free-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)

    attempts = [
        item
        async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "dead-model", "messages": []},
            ["free-good:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["free-good:free"]


@pytest.mark.asyncio
async def test_payload_attempts_respects_max_fallback_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_max_fallback_attempts=1,
    )
    client = OpenRouterClient(settings)

    async def fake_model_ids() -> set[str]:
        return {"primary", "fallback-one:free", "fallback-two:free"}

    monkeypatch.setattr(client, "model_ids", fake_model_ids)
    attempts = [
        item
        async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "primary", "messages": []},
            ["fallback-one:free", "fallback-two:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["primary", "fallback-one:free"]


@pytest.mark.asyncio
async def test_chat_completion_does_not_call_invalid_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    attempts = [
        item async for item in client._payload_attempts(payload, ["router/free-alias:free"])
    ]

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


@pytest.mark.asyncio
async def test_chat_completion_returns_structured_failure_instead_of_raising_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(openrouter_api_key="test", openrouter_free_fallback_model="free-good:free")
    client = OpenRouterClient(settings)

    async def fake_attempts(payload, fallback_models):
        yield {**payload, "model": "dead-model"}

    async def fake_post_json(payload):
        return {"_retryable_model_error": True, "status_code": 404, "message": "dead"}

    monkeypatch.setattr(client, "_payload_attempts", fake_attempts)
    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = await client.chat_completion(
        {"model": "dead-model", "messages": []}, ["free-good:free"]
    )

    assert result["_all_attempts_failed"] is True
    assert result["hive_attempts"] == [
        {"model": "dead-model", "status_code": 404, "message": "dead"}
    ]


@pytest.mark.asyncio
async def test_free_only_mode_reorders_non_free_primary_behind_free_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test",
        allow_paid_fallback=False,
        openrouter_free_fallback_model="free-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)

    async def fake_ids():
        return {"dead-paid", "free-good-dated:free"}

    monkeypatch.setattr(client, "model_ids", fake_ids)

    attempts = [
        item
        async for item in client._payload_attempts(  # noqa: SLF001 - behaviour-level unit test
            {"model": "dead-paid", "messages": []},
            ["free-good:free"],
        )
    ]

    assert [attempt["model"] for attempt in attempts] == ["free-good:free"]


@pytest.mark.asyncio
async def test_chat_completion_retries_empty_visible_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_free_fallback_model="fallback-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)
    called_models: list[str] = []

    async def fake_attempts(payload, fallback_models):  # noqa: ANN001
        yield {**payload, "model": "empty-model:free"}
        yield {**payload, "model": "fallback-good:free"}

    async def fake_post_json(payload):  # noqa: ANN001
        called_models.append(payload["model"])
        if payload["model"] == "empty-model:free":
            return {
                "model": "empty-model:free",
                "choices": [{"message": {"content": None}, "finish_reason": "length"}],
            }
        return {
            "model": "fallback-good:free",
            "choices": [{"message": {"content": "visible reply"}, "finish_reason": "stop"}],
        }

    monkeypatch.setattr(client, "_payload_attempts", fake_attempts)
    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = await client.chat_completion(
        {"model": "empty-model:free", "messages": []}, ["fallback-good:free"]
    )

    assert result["model"] == "fallback-good:free"
    assert called_models == ["empty-model:free", "fallback-good:free"]
    assert result["hive_attempts"][0]["empty_reply"] is True
    assert result["choices"][0]["message"]["content"] == "visible reply"


@pytest.mark.asyncio
async def test_chat_completion_returns_empty_reply_diagnostic_when_all_attempts_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test-key", openrouter_free_fallback_model="empty-free:free"
    )
    client = OpenRouterClient(settings)

    async def fake_attempts(payload, fallback_models):  # noqa: ANN001
        yield {**payload, "model": "empty-free:free"}

    async def fake_post_json(payload):  # noqa: ANN001, ARG001
        return {
            "model": "empty-free:free",
            "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        }

    monkeypatch.setattr(client, "_payload_attempts", fake_attempts)
    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = await client.chat_completion({"model": "empty-free:free", "messages": []}, [])

    assert result["_all_attempts_failed"] is True
    assert result["_empty_model_reply"] is True
    assert result["hive_error_code"] == "empty_model_reply"
    assert result["choices"][0]["finish_reason"] == "empty_reply"


@pytest.mark.asyncio
async def test_chat_completion_retries_retryable_bad_request_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_free_fallback_model="fallback-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)
    called_models: list[str] = []

    async def fake_attempts(payload, fallback_models):  # noqa: ANN001, ARG001
        yield {**payload, "model": "bad-primary"}
        yield {**payload, "model": "fallback-good:free"}

    async def fake_post_json(payload):  # noqa: ANN001
        called_models.append(str(payload["model"]))
        if payload["model"] == "bad-primary":
            return {
                "_retryable_model_error": True,
                "status_code": 400,
                "message": "provider rejected this model",
            }
        return {
            "model": "fallback-good:free",
            "choices": [{"message": {"content": "fallback reply"}, "finish_reason": "stop"}],
        }

    monkeypatch.setattr(client, "_payload_attempts", fake_attempts)
    monkeypatch.setattr(client, "_post_json", fake_post_json)

    result = await client.chat_completion(
        {"model": "bad-primary", "messages": []}, ["fallback-good:free"]
    )

    assert result["model"] == "fallback-good:free"
    assert called_models == ["bad-primary", "fallback-good:free"]
    assert result["hive_attempts"][0]["status_code"] == 400
    assert result["choices"][0]["message"]["content"] == "fallback reply"


@pytest.mark.asyncio
async def test_stream_chat_completion_retries_provider_error_before_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_free_fallback_model="fallback-good:free",
        openrouter_max_fallback_attempts=2,
    )
    client = OpenRouterClient(settings)
    attempted_models: list[str] = []

    async def fake_attempts(payload, fallback_models):  # noqa: ANN001, ARG001
        yield {**payload, "model": "bad-primary"}
        yield {**payload, "model": "fallback-good:free"}

    async def fake_stream_one_attempt(payload):  # noqa: ANN001
        attempted_models.append(str(payload["model"]))
        if payload["model"] == "bad-primary":
            yield {
                "event": "retry_model",
                "message": "provider stream error",
                "model_used": "bad-primary",
            }
            return
        yield {"event": "token", "content": "visible", "model_used": "fallback-good:free"}
        yield {"event": "done", "ok": True, "model_used": "fallback-good:free"}

    monkeypatch.setattr(client, "_payload_attempts", fake_attempts)
    monkeypatch.setattr(client, "_stream_one_attempt", fake_stream_one_attempt)

    events = [
        event
        async for event in client.stream_chat_completion(
            {"model": "bad-primary", "messages": []}, ["fallback-good:free"]
        )
    ]

    assert attempted_models == ["bad-primary", "fallback-good:free"]
    assert events[0] == {
        "event": "meta",
        "type": "model_fallback",
        "from_model": "bad-primary",
        "message": "provider stream error",
    }
    assert events[-1]["event"] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["model_used"] == "fallback-good:free"
