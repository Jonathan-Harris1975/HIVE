from __future__ import annotations

from pathlib import Path

import pytest

from app.api import chat as chat_api
from app.api.chat import ChatRequest
from app.core.config import Settings
from app.storage.sql_store import SqlStore


class FakeStreamingClient:
    def __init__(self, settings: Settings) -> None:  # noqa: ARG002
        pass

    async def stream_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG002
        yield {"event": "token", "content": "Hello "}
        yield {"event": "token", "content": "from HIVE"}
        yield {
            "event": "done",
            "ok": True,
            "model_used": "test/model",
            "provider": "test-provider",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "cost": 0.002},
        }


@pytest.mark.asyncio
async def test_stream_chat_persists_completed_turn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    monkeypatch.setattr(chat_api, "OpenRouterClient", FakeStreamingClient)

    request = ChatRequest(
        message="Start a persisted stream",
        mode="general",
        model="test/model",
        conversation_id="stream-conversation",
    )
    payload, fallbacks = chat_api.build_payload(request, settings)
    events = [
        event
        async for event in chat_api._stream_and_record_chat(  # noqa: SLF001
            request=request,
            payload=payload,
            fallback_models=fallbacks,
            settings=settings,
        )
    ]

    assert events[0] == {
        "event": "meta",
        "type": "conversation",
        "conversation_id": "stream-conversation",
    }
    assert events[-1]["event"] == "done"
    assert events[-1]["db_recorded"] is True
    assert events[-1]["conversation_id"] == "stream-conversation"

    saved = store.get_conversation("stream-conversation")
    assert saved["ok"] is True
    assert saved["conversation"]["title"] == "Start a persisted stream"
    assert [message["content"] for message in saved["messages"]] == [
        "Start a persisted stream",
        "Hello from HIVE",
    ]


class FakeTruncatedStreamingClient:
    def __init__(self, settings: Settings) -> None:  # noqa: ARG002
        pass

    async def stream_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG002
        yield {"event": "token", "content": "Partial but useful"}
        yield {
            "event": "done",
            "ok": True,
            "model_used": "test/model",
            "provider": "test-provider",
            "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5, "cost": 0.002},
            "finish_reason": "length",
            "completion_truncated": True,
        }


@pytest.mark.asyncio
async def test_stream_chat_exposes_and_records_truncation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "hive-truncated.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    monkeypatch.setattr(chat_api, "OpenRouterClient", FakeTruncatedStreamingClient)

    request = ChatRequest(
        message="Give me a long answer",
        mode="general",
        model="test/model",
        conversation_id="truncated-stream-conv",
    )
    payload, fallbacks = chat_api.build_payload(request, settings)
    events = [
        event
        async for event in chat_api._stream_and_record_chat(  # noqa: SLF001
            request=request,
            payload=payload,
            fallback_models=fallbacks,
            settings=settings,
        )
    ]

    assert events[-1]["db_recorded"] is True
    assert events[-1]["finish_reason"] == "length"
    assert events[-1]["completion_truncated"] is True
    saved = store.get_conversation("truncated-stream-conv")
    metadata = saved["messages"][-1]["metadata"]
    assert metadata["completion_truncated"] is True


@pytest.mark.asyncio
async def test_interrupted_stream_retains_partial_text_and_known_fallback_cost(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True, DATABASE_URL=f"sqlite:///{tmp_path / 'interrupted.sqlite'}", OPENROUTER_API_KEY="test")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]

    class InterruptedClient:
        def __init__(self, settings):
            pass

        async def stream_chat_completion(self, payload, fallback_models=None):
            yield {"event": "meta", "type": "empty_reply_retry", "usage_attempt": {"model": "first", "provider": "p", "usage": {"cost": 0.01}}}
            yield {"event": "token", "content": "partial"}
            yield {"event": "token", "content": "not seen"}

    monkeypatch.setattr(chat_api, "OpenRouterClient", InterruptedClient)
    request = ChatRequest(message="Test interruption", mode="general", conversation_id="interrupted")
    stream = chat_api._stream_and_record_chat(request=request, payload={}, fallback_models=[], settings=settings)
    assert (await anext(stream))["type"] == "conversation"
    assert (await anext(stream))["type"] == "empty_reply_retry"
    assert (await anext(stream))["content"] == "partial"
    await stream.aclose()
    saved = store.get_conversation("interrupted")
    assert saved["messages"][-1]["content"] == "partial"
    assert saved["messages"][-1]["metadata"]["stream_ended_early"] is True
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] is None
    costs = store.cost_summary()
    assert costs["ok"] is True
    assert costs["totals"]["cost_usd"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_cancelled_stream_before_first_provider_event_is_persisted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{tmp_path / 'cancelled-before-event.sqlite'}",
        OPENROUTER_API_KEY="test",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"]

    class CancelledBeforeEventClient:
        def __init__(self, settings):  # noqa: ANN001, ARG002
            pass

        async def stream_chat_completion(self, payload, fallback_models=None):  # noqa: ANN001, ARG002
            if False:
                yield {}

    monkeypatch.setattr(chat_api, "OpenRouterClient", CancelledBeforeEventClient)
    request = ChatRequest(message="Stop immediately", mode="general", conversation_id="cancelled-before-event")
    stream = chat_api._stream_and_record_chat(request=request, payload={}, fallback_models=[], settings=settings)

    assert (await anext(stream))["type"] == "conversation"
    await stream.aclose()

    saved = store.get_conversation("cancelled-before-event")
    assert saved["ok"] is True
    assert [message["role"] for message in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][-1]["content"] == ""
    assert saved["messages"][-1]["metadata"]["ok"] is False
    assert saved["messages"][-1]["metadata"]["client_disconnect_or_cancel"] is True
    assert saved["messages"][-1]["cost_usd"] is None
