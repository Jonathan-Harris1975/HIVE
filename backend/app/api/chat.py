from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.core.sse import heartbeat_stream
from app.services.brand_modes import build_system_prompt
from app.services.context_manager import ContextWindow
from app.services.model_router import Mode, ModelRouter
from app.services.openrouter import OpenRouterClient

router = APIRouter(tags=["chat"], dependencies=[Depends(require_admin)])


class ChatTurn(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatTurn] = Field(default_factory=list)
    mode: Mode = Mode.AUTO
    model: str | None = None
    temperature: float = 0.4
    max_tokens: int = 2048


def build_payload(request: ChatRequest, settings: Settings) -> tuple[dict[str, object], list[str]]:
    router_service = ModelRouter(settings)
    task = router_service.classify_task(request.message, request.mode)
    effective_mode = router_service.resolve_mode(task, request.mode)
    selected_model = router_service.select_model(task, request.model)
    fallback_models = router_service.fallback_models_for_task(task, selected_model)

    window = ContextWindow()
    window.add("system", build_system_prompt(effective_mode))
    for turn in request.history:
        window.add(turn.role, turn.content)
    window.add("user", request.message)

    payload: dict[str, object] = {
        "model": selected_model,
        "messages": window.trimmed_messages(),
        "temperature": request.temperature,
        "max_tokens": max(request.max_tokens, settings.openrouter_min_response_tokens),
    }
    return payload, fallback_models


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    payload, fallback_models = build_payload(request, settings)
    client = OpenRouterClient(settings)
    stream = heartbeat_stream(client.stream_chat_completion(payload, fallback_models=fallback_models))
    return StreamingResponse(stream, media_type="text/event-stream")


@router.post("/chat")
async def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Non-streaming endpoint for Make.com and smoke tests."""

    payload, fallback_models = build_payload(request, settings)
    client = OpenRouterClient(settings)
    completion = await client.chat_completion(payload, fallback_models=fallback_models)
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reply = _reply_text(message.get("content"))
    finish_reason = choice.get("finish_reason")
    empty_reply = not reply.strip()
    ok = not bool(completion.get("_all_attempts_failed")) and not empty_reply
    return {
        "ok": ok,
        "reply": reply,
        "model_used": completion.get("model") or payload.get("model"),
        "provider": completion.get("provider"),
        "usage": completion.get("usage"),
        "raw_finish_reason": finish_reason,
        "completion_truncated": finish_reason == "length",
        "empty_reply": empty_reply,
        "error_code": completion.get("hive_error_code"),
        "attempts": completion.get("hive_attempts"),
    }


def _reply_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)
