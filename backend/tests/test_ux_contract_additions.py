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
    monkeypatch.setattr(chat_api, "OpenRouterClient", _FakeOpenRouterClient)

    response = await chat_api.auto_title_conversation("conv-title", settings=settings)

    assert response.ok is True
    assert response.title == "R2 File Selection Upgrade"
    conversation = store.get_conversation("conv-title")
    assert conversation["conversation"]["title"] == "R2 File Selection Upgrade"
    assert conversation["conversation"]["auto_titled"] is True
    list_response = store.list_conversations()
    assert list_response["conversations"][0]["auto_titled"] is True
    assert list_response["conversations"][0]["total_tokens"] == 10
    assert list_response["conversations"][0]["total_cost_usd"] == 0


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
