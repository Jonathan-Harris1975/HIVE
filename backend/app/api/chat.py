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


class ChatStreamRequest(BaseModel):
    message: str
    history: list[ChatTurn] = Field(default_factory=list)
    mode: Mode = Mode.AUTO
    model: str | None = None
    temperature: float = 0.4
    max_tokens: int = 2048


@router.post("/chat/stream")
async def chat_stream(
    request: ChatStreamRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    router_service = ModelRouter(settings)
    task = router_service.classify_task(request.message, request.mode)
    selected_model = router_service.select_model(task, request.model)

    window = ContextWindow()
    window.add("system", build_system_prompt(request.mode))
    for turn in request.history:
        window.add(turn.role, turn.content)
    window.add("user", request.message)

    payload = {
        "model": selected_model,
        "messages": window.trimmed_messages(),
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }

    client = OpenRouterClient(settings)
    stream = heartbeat_stream(client.stream_chat_completion(payload))
    return StreamingResponse(stream, media_type="text/event-stream")
