from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

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
from app.services.skill_registry import build_skill_context
from app.storage.sql_store import SqlStore

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
    conversation_id: str | None = None
    use_persisted_history: bool = True
    db_history_limit: int = Field(20, ge=0, le=100)
    test_run_id: str | None = Field(None, max_length=120)
    use_skills: bool = True
    skill_repo: str | None = Field(None, max_length=120)
    skill_lane: str | None = Field(None, max_length=120)
    skill_risk_ceiling: Literal["low", "medium", "high"] | None = None
    skill_limit: int | None = Field(None, ge=1, le=8)


def build_payload(request: ChatRequest, settings: Settings) -> tuple[dict[str, object], list[str]]:
    payload, fallbacks, _skill_context = build_payload_with_context(request, settings)
    return payload, fallbacks


def build_payload_with_context(
    request: ChatRequest,
    settings: Settings,
) -> tuple[dict[str, object], list[str], dict[str, object]]:
    router_service = ModelRouter(settings)
    task = router_service.classify_task(request.message, request.mode)
    effective_mode = router_service.resolve_mode(task, request.mode)
    selected_model = router_service.select_model(task, request.model)
    fallback_models = router_service.fallback_models_for_task(task, selected_model)

    window = ContextWindow()
    window.add("system", build_system_prompt(effective_mode))
    skill_context = (
        build_skill_context(
            settings=settings,
            task=request.message,
            repo=request.skill_repo,
            hive_lane=request.skill_lane,
            risk_ceiling=request.skill_risk_ceiling,
            limit=request.skill_limit,
        )
        if request.use_skills
        else {"ok": True, "enabled": False, "prompt": "", "skills": []}
    )
    skill_prompt = skill_context.get("prompt")
    if isinstance(skill_prompt, str) and skill_prompt:
        window.add("system", skill_prompt)

    if request.conversation_id and request.use_persisted_history and request.db_history_limit > 0:
        for turn in SqlStore(settings).recent_chat_turns(
            request.conversation_id,
            limit=request.db_history_limit,
        ):
            window.add(turn["role"], turn["content"])

    for turn in request.history:
        window.add(turn.role, turn.content)
    window.add("user", request.message)

    payload: dict[str, object] = {
        "model": selected_model,
        "messages": window.trimmed_messages(),
        "temperature": request.temperature,
        "max_tokens": max(request.max_tokens, settings.openrouter_min_response_tokens),
    }
    return payload, fallback_models, skill_context


@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream a chat response and persist the completed turn.

    A conversation identifier is allocated before the first token and emitted as a
    ``meta`` SSE event. The final ``done`` event includes persistence status so the
    frontend can update its sidebar without issuing a second write request.
    """

    conversation_id = request.conversation_id or str(uuid.uuid4())
    request_with_id = request.model_copy(update={"conversation_id": conversation_id})
    payload, fallback_models, skill_context = build_payload_with_context(request_with_id, settings)
    stream = _stream_and_record_chat(
        request=request_with_id,
        payload=payload,
        fallback_models=fallback_models,
        settings=settings,
        skill_context=skill_context,
    )
    return StreamingResponse(heartbeat_stream(stream), media_type="text/event-stream")


async def _stream_and_record_chat(
    *,
    request: ChatRequest,
    payload: dict[str, object],
    fallback_models: list[str],
    settings: Settings,
    skill_context: dict[str, object] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    conversation_id = request.conversation_id or str(uuid.uuid4())
    client = OpenRouterClient(settings)
    reply_parts: list[str] = []
    recorded = False

    meta_event: dict[str, Any] = {
        "event": "meta",
        "type": "conversation",
        "conversation_id": conversation_id,
    }
    if skill_context is not None:
        meta_event["skills_used"] = _skill_summaries(skill_context)
        meta_event["skill_context_status"] = _skill_context_status(skill_context)
    yield meta_event

    try:
        async for event in client.stream_chat_completion(payload, fallback_models=fallback_models):
            event_name = str(event.get("event") or "message")
            if event_name == "token":
                content = event.get("content")
                if isinstance(content, str):
                    reply_parts.append(content)
                yield event
                continue

            if event_name == "done":
                result = _record_streamed_turn(
                    request=request,
                    event=event,
                    reply="".join(reply_parts),
                    conversation_id=conversation_id,
                    settings=settings,
                    stream_ended_early=False,
                    skill_context=skill_context,
                )
                recorded = True
                yield {
                    **event,
                    "conversation_id": conversation_id,
                    "db_recorded": bool(result.get("ok")),
                    "db_error": result.get("error"),
                    "skills_used": _skill_summaries(skill_context),
                    "skill_context_status": _skill_context_status(skill_context),
                }
                return

            yield event
    finally:
        # A browser tab can close halfway through an SSE response. Preserve any text
        # already generated so the database and conversation list do not silently
        # diverge from what the user saw.
        if not recorded and reply_parts:
            _record_streamed_turn(
                request=request,
                event={"ok": False},
                reply="".join(reply_parts),
                conversation_id=conversation_id,
                settings=settings,
                stream_ended_early=True,
                skill_context=skill_context,
            )


def _record_streamed_turn(
    *,
    request: ChatRequest,
    event: dict[str, Any],
    reply: str,
    conversation_id: str,
    settings: Settings,
    stream_ended_early: bool,
    skill_context: dict[str, object] | None = None,
) -> dict[str, object]:
    usage = event.get("usage")
    finish_ok = bool(event.get("ok")) and not stream_ended_early
    return SqlStore(settings).record_chat(
        conversation_id=conversation_id,
        mode=str(request.mode),
        user_message=request.message,
        assistant_reply=reply,
        model_used=str(event.get("model_used")) if event.get("model_used") else None,
        provider=str(event.get("provider")) if event.get("provider") else None,
        usage=usage if isinstance(usage, dict) else None,
        metadata={
            "endpoint": "/v1/chat/stream",
            "ok": finish_ok,
            "stream_ended_early": stream_ended_early,
            "test_run_id": request.test_run_id,
            "skills_used": _skill_summaries(skill_context),
            "skill_context_status": _skill_context_status(skill_context),
        },
    )


@router.post("/chat")
async def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Non-streaming endpoint for Make.com and smoke tests."""

    payload, fallback_models, skill_context = build_payload_with_context(request, settings)
    client = OpenRouterClient(settings)
    completion = await client.chat_completion(payload, fallback_models=fallback_models)
    choice = (completion.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    reply = _reply_text(message.get("content"))
    finish_reason = choice.get("finish_reason")
    empty_reply = not reply.strip()
    ok = not bool(completion.get("_all_attempts_failed")) and not empty_reply
    model_used = completion.get("model") or payload.get("model")
    provider = completion.get("provider")
    usage = completion.get("usage")
    db_record = SqlStore(settings).record_chat(
        conversation_id=request.conversation_id,
        mode=str(request.mode),
        user_message=request.message,
        assistant_reply=reply,
        model_used=str(model_used) if model_used else None,
        provider=str(provider) if provider else None,
        usage=usage if isinstance(usage, dict) else None,
        metadata={
            "endpoint": "/v1/chat",
            "ok": ok,
            "finish_reason": finish_reason,
            "empty_reply": empty_reply,
            "test_run_id": request.test_run_id,
        },
    )
    return {
        "ok": ok,
        "reply": reply,
        "model_used": model_used,
        "provider": provider,
        "usage": usage,
        "raw_finish_reason": finish_reason,
        "completion_truncated": finish_reason == "length",
        "empty_reply": empty_reply,
        "error_code": completion.get("hive_error_code"),
        "attempts": completion.get("hive_attempts"),
        "conversation_id": db_record.get("conversation_id") or request.conversation_id,
        "db_recorded": bool(db_record.get("ok")),
        "db_error": db_record.get("error"),
        "skills_used": _skill_summaries(skill_context),
        "skill_context_status": _skill_context_status(skill_context),
    }


def _skill_summaries(skill_context: dict[str, object] | None) -> list[dict[str, object]]:
    if not isinstance(skill_context, dict):
        return []
    skills = skill_context.get("skills")
    return [item for item in skills if isinstance(item, dict)] if isinstance(skills, list) else []


def _skill_context_status(skill_context: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(skill_context, dict):
        return {"enabled": False, "ok": True, "source": None, "fallback_reason": None}
    return {
        "enabled": bool(skill_context.get("enabled")),
        "ok": bool(skill_context.get("ok")),
        "source": skill_context.get("source"),
        "fallback_reason": skill_context.get("fallback_reason"),
        "error_code": skill_context.get("error_code"),
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
