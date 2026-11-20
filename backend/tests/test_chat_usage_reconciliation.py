from __future__ import annotations

import httpx
import pytest

from app.core.config import Settings
from app.services.chat_usage_reconciliation import reconcile_conversation_usage
from app.storage.sql_store import SqlStore


@pytest.mark.asyncio
async def test_stream_rate_limit_falls_back_once_without_duplicate_text(monkeypatch):
    from app.services.openrouter import OpenRouterClient
    settings = Settings(OPENROUTER_API_KEY="test")
    client = OpenRouterClient(settings)
    called = []

    async def candidates(payload, fallbacks):
        yield {"model": "primary"}
        yield {"model": "fallback"}

    async def routes(payload):
        yield "openrouter", "https://provider.test/chat", {}, payload

    def serve(request):
        import json
        model = json.loads(request.content)["model"]
        called.append(model)
        if model == "primary":
            return httpx.Response(429, text="rate limited", request=request)
        return httpx.Response(200, request=request, text=(
            'data: {"id":"gen-ok","model":"fallback","choices":[{"delta":{"content":"hello"}}]}\n\n'
            'data: {"id":"gen-ok","model":"fallback","choices":[{"delta":{},"finish_reason":"stop"}],'
            '"usage":{"total_tokens":2,"cost":0.01}}\n\n'
            'data: [DONE]\n\n'))

    original_client = httpx.AsyncClient
    monkeypatch.setattr(client, "_payload_attempts", candidates)
    monkeypatch.setattr(client, "_context_attempts", routes)
    monkeypatch.setattr("app.services.openrouter.httpx.AsyncClient",
                        lambda **kwargs: original_client(transport=httpx.MockTransport(serve), trust_env=False))
    events = [event async for event in client.stream_chat_completion({}, [])]
    assert called == ["primary", "fallback"]
    assert [event["content"] for event in events if event["event"] == "token"] == ["hello"]
    assert any(event.get("generation_id") == "gen-ok" for event in events)
    assert events[-1]["ok"] is True


@pytest.mark.asyncio
async def test_provider_error_after_tokens_does_not_duplicate_unknown_usage(monkeypatch):
    from app.services.openrouter import OpenRouterClient
    client = OpenRouterClient(Settings(OPENROUTER_API_KEY="test"))

    async def candidates(payload, fallbacks):
        yield {"model": "primary"}
        yield {"model": "fallback"}

    async def attempt(payload):
        yield {"event": "meta", "type": "generation", "generation_id": "gen-partial"}
        yield {"event": "token", "content": "partial"}
        yield {"event": "retry_model", "message": "provider disconnected", "model_used": "primary"}

    monkeypatch.setattr(client, "_payload_attempts", candidates)
    monkeypatch.setattr(client, "_stream_one_attempt", attempt)
    events = [event async for event in client.stream_chat_completion({}, [])]
    assert events[-1]["ok"] is False
    assert events[-1]["partial_response"] is True
    assert events[-1]["hive_extra_usage"] == []
    assert [event["content"] for event in events if event["event"] == "token"] == ["partial"]


@pytest.mark.asyncio
async def test_interrupted_stream_saves_generation_id_for_later_recovery(tmp_path, monkeypatch):
    from app.api.chat import ChatRequest, _stream_and_record_chat
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True,
                        DATABASE_URL=f"sqlite:///{tmp_path / 'chat.sqlite'}", OPENROUTER_API_KEY="test")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]

    class FakeClient:
        def __init__(self, settings): pass
        async def stream_chat_completion(self, payload, fallback_models):
            yield {"event": "meta", "type": "generation", "generation_id": "gen-test", "model_used": "m"}
            yield {"event": "token", "content": "partial"}

    monkeypatch.setattr("app.api.chat.OpenRouterClient", FakeClient)
    stream = _stream_and_record_chat(request=ChatRequest(message="hello", conversation_id="conv"),
                                     payload={}, fallback_models=[], settings=settings)
    assert (await anext(stream))["type"] == "conversation"
    assert (await anext(stream))["generation_id"] == "gen-test"
    assert (await anext(stream))["content"] == "partial"
    await stream.aclose()
    saved = store.get_conversation("conv")["messages"][-1]
    assert saved["metadata"]["stream_ended_early"] is True
    assert saved["metadata"]["generation_id"] == "gen-test"
    assert saved["cost_usd"] is None


@pytest.mark.asyncio
async def test_transport_failure_after_text_is_recoverable(tmp_path, monkeypatch):
    from app.api.chat import ChatRequest, _stream_and_record_chat
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True,
                        DATABASE_URL=f"sqlite:///{tmp_path / 'chat.sqlite'}", OPENROUTER_API_KEY="test")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]

    class FakeClient:
        def __init__(self, settings): pass
        async def stream_chat_completion(self, payload, fallback_models):
            yield {"event": "meta", "type": "generation", "generation_id": "gen-transport"}
            yield {"event": "token", "content": "partial"}
            yield {"event": "done", "ok": False}

    monkeypatch.setattr("app.api.chat.OpenRouterClient", FakeClient)
    events = [e async for e in _stream_and_record_chat(
        request=ChatRequest(message="hello", conversation_id="conv"), payload={},
        fallback_models=[], settings=settings)]
    assert events[-1]["ok"] is False
    metadata = store.get_conversation("conv")["messages"][-1]["metadata"]
    assert metadata["stream_ended_early"] is True
    assert metadata["generation_id"] == "gen-transport"


def test_reconciliation_can_reach_older_unavailable_turns(tmp_path, monkeypatch):
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True,
                        DATABASE_URL=f"sqlite:///{tmp_path / 'chat.sqlite'}", OPENROUTER_API_KEY="test")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]
    for index in range(5):
        assert store.record_chat(conversation_id="conv", mode="general", user_message="hello",
                                 assistant_reply="partial", model_used="m", provider=None, usage=None,
                                 metadata={"stream_ended_early": True, "generation_id": f"gen-{index}"})["ok"]
    requested = []
    def unavailable(url, *, params, **kwargs):
        requested.append(params["id"])
        return httpx.Response(404, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "get", unavailable)
    monkeypatch.setattr("app.services.chat_usage_reconciliation.secrets.randbelow", lambda n: 3)
    assert not reconcile_conversation_usage(settings, store, "conv", [])
    assert len(requested) == 3
    assert "gen-3" in requested


def test_interrupted_usage_is_recovered_once_and_unavailable_usage_stays_unknown(tmp_path, monkeypatch):
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True,
                        DATABASE_URL=f"sqlite:///{tmp_path / 'chat.sqlite'}", OPENROUTER_API_KEY="test")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]
    assert store.record_chat(conversation_id="test", mode="general", user_message="hello",
                             assistant_reply="partial", model_used="m", provider=None, usage=None,
                             metadata={"stream_ended_early": True, "generation_id": "gen-abc"})["ok"]
    messages = store.get_conversation("test")["messages"]
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] is None

    def missing(*args, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", "https://provider.test"))
    monkeypatch.setattr(httpx, "get", missing)
    assert not reconcile_conversation_usage(settings, store, "test", messages)
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] is None

    def ready(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("GET", "https://provider.test"), json={
            "data": {"id": "gen-abc", "cancelled": True, "model": "actual/model",
                     "provider_name": "provider", "tokens_prompt": 17,
                     "tokens_completion": 11, "total_cost": 0.003}})
    monkeypatch.setattr(httpx, "get", ready)
    assert reconcile_conversation_usage(settings, store, "test", messages)
    assert reconcile_conversation_usage(settings, store, "test", messages)
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] == 0.003
    result = store.get_conversation("test")
    assert result["messages"][-1]["model"] == "actual/model"
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM hive_cost_events").fetchone()[0] == 1
