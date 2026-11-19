from __future__ import annotations

from pathlib import Path

import pytest

from app.api import chat as chat_api
from app.core.config import Settings
from app.services.repo_health import _with_readiness
from app.storage.sql_store import SqlStore


class _FakeOpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def chat_completion(self, payload: dict[str, object], fallback_models: list[str] | None = None) -> dict[str, object]:
        return {"choices": [{"message": {"content": "R2 File Selection Upgrade."}}]}


@pytest.mark.asyncio
async def test_auto_title_conversation_persists_clean_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test-key",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    assert store.record_chat(
        conversation_id="conv-title",
        mode="general",
        user_message="How do I select multiple R2 files for chat?",
        assistant_reply="Use the file picker and pass selected R2 object references into the chat payload.",
        model_used="test/model",
        provider="test-provider",
        usage={"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10, "cost": 0.0},
    )["ok"] is True
    title_requests = []

    class CapturingClient(_FakeOpenRouterClient):
        async def chat_completion(self, payload, fallback_models=None):
            title_requests.append((payload, fallback_models))
            return await super().chat_completion(payload, fallback_models)

    monkeypatch.setattr(chat_api, "OpenRouterClient", CapturingClient)
    monkeypatch.setattr(chat_api.ModelRouter, "select_title_model", lambda self: "paid/title-economy")

    response = await chat_api.auto_title_conversation("conv-title", settings=settings)

    assert response.ok is True
    assert response.title == "R2 File Selection Upgrade"
    assert title_requests[0][0]["model"] == "paid/title-economy"
    assert not any(model.endswith(":free") for model in title_requests[0][1])
    conversation = store.get_conversation("conv-title")
    assert conversation["conversation"]["title"] == "R2 File Selection Upgrade"
    assert conversation["conversation"]["auto_titled"] is True
    list_response = store.list_conversations()
    assert list_response["conversations"][0]["auto_titled"] is True
    assert list_response["conversations"][0]["total_tokens"] == 10
    # The title provider omitted usage; the total must not imply zero cost.
    assert list_response["conversations"][0]["total_cost_usd"] is None


@pytest.mark.asyncio
async def test_auto_title_does_not_overwrite_manual_title(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test-key",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    assert store.record_chat(
        conversation_id="conv-manual",
        mode="general",
        user_message="How do I select multiple R2 files for chat?",
        assistant_reply="Use the file picker and pass selected R2 object references into the chat payload.",
        model_used="test/model",
        provider="test-provider",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.0},
    )["ok"] is True
    assert store.rename_conversation("conv-manual", "Manual operator title")["ok"] is True
    monkeypatch.setattr(chat_api, "OpenRouterClient", _FakeOpenRouterClient)

    response = await chat_api.auto_title_conversation("conv-manual", settings=settings)

    assert response.ok is True
    assert response.skipped is True
    assert response.auto_titled is False
    conversation = store.get_conversation("conv-manual")
    assert conversation["conversation"]["title"] == "Manual operator title"
    assert conversation["conversation"]["auto_titled"] is False


def test_repo_health_readiness_is_derived_from_operational_status() -> None:
    item = {
        "repo": "AIMS",
        "status": "degraded",
        "operational": {
            "status": "degraded",
            "configured": True,
            "detail": "Operational API reported partial capability.",
        },
    }

    enriched = _with_readiness(item)

    assert enriched["readiness"]["status"] == "partial"
    assert enriched["readiness"]["detail"] == "Operational API reported partial capability."


@pytest.mark.asyncio
async def test_failed_auto_title_does_not_invent_an_unknown_cost_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True, DATABASE_URL=f"sqlite:///{tmp_path / 'title.sqlite'}", OPENROUTER_API_KEY="test")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]
    assert store.record_chat(conversation_id="title-fail", mode="general", user_message="hello", assistant_reply="world",
                             model_used="chat", provider="p", usage={"cost": 0.01})["ok"]

    class FailedTitleClient:
        def __init__(self, settings): pass
        async def chat_completion(self, payload, fallback_models=None):
            return {"_all_attempts_failed": True, "usage": None, "hive_extra_usage": [
                {"model": "title", "provider": "p", "usage": {"cost": 0.02}}],
                "choices": [{"message": {"content": "OpenRouter returned no visible assistant text."}}]}

    monkeypatch.setattr(chat_api, "OpenRouterClient", FailedTitleClient)
    response = await chat_api.auto_title_conversation("title-fail", settings=settings)
    assert response.skipped is True
    assert response.ok is False
    assert store.get_conversation("title-fail")["conversation"]["title"] == "hello"
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] == pytest.approx(0.03)
