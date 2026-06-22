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
