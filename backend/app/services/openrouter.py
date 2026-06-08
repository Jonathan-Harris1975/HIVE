from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings


@dataclass(frozen=True)
class OpenRouterStreamState:
    model_used: str | None = None
    provider: str | None = None
    usage: dict[str, Any] | None = None
    raw_final_chunk: dict[str, Any] | None = None


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._models_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._cache_seconds = 10 * 60

    def _headers(self) -> dict[str, str]:
        if not self.settings.openrouter_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="OpenRouter API key is not configured",
            )
        return {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "HTTP-Referer": self.settings.openrouter_site_url,
            "X-Title": self.settings.openrouter_app_title,
            "Content-Type": "application/json",
        }

    async def validate_key(self) -> bool:
        await self.list_models(force_refresh=True)
        return True

    async def list_models(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        if not force_refresh and self._models_cache and now - self._models_cache[0] < self._cache_seconds:
            return self._models_cache[1]

        url = f"{self.settings.openrouter_base_url.rstrip('/')}/models"
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            self._models_cache = (now, models)
            return models

    async def chat_completion(self, payload: dict[str, Any], fallback_models: list[str] | None = None) -> dict[str, Any]:
        """Non-streaming chat completion for Make.com and simple smoke tests."""

        async for candidate_payload in self._payload_attempts(payload, fallback_models):
            response_payload = await self._post_json(candidate_payload)
            if response_payload.get("_retryable_model_error"):
                continue
            return response_payload

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="OpenRouter request failed for selected model and all fallback models",
        )

    async def stream_chat_completion(
        self,
        payload: dict[str, Any],
        fallback_models: list[str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream clean SSE-ready chunks.

        Emits:
        - keepalive events from OpenRouter comment lines
        - token events containing only assistant text deltas
        - meta events when a model fallback happens
        - one done event with model/provider/usage metadata
        """

        async for candidate_payload in self._payload_attempts(payload, fallback_models):
            saw_token = False
            state = OpenRouterStreamState(model_used=candidate_payload.get("model"))

            async for event in self._stream_one_attempt(candidate_payload):
                if event.get("event") == "retry_model":
                    if saw_token:
                        # Once tokens have left the server, we cannot safely replay into the same stream.
                        yield {"event": "error", "message": event["message"], "model": candidate_payload.get("model")}
                        yield {"event": "done", "ok": False, "model_used": state.model_used}
                        return
                    yield {
                        "event": "meta",
                        "type": "model_fallback",
                        "from_model": candidate_payload.get("model"),
                        "message": event["message"],
                    }
                    break

                if event.get("event") == "token":
                    saw_token = True
                    yield event
                    continue

                if event.get("event") == "done":
                    state = OpenRouterStreamState(
                        model_used=event.get("model_used") or state.model_used,
                        provider=event.get("provider"),
                        usage=event.get("usage"),
                        raw_final_chunk=event.get("raw_final_chunk"),
                    )
                    yield {
                        "event": "done",
                        "ok": True,
                        "model_used": state.model_used,
                        "provider": state.provider,
                        "usage": state.usage,
                    }
                    return

                yield event
        yield {"event": "done", "ok": False, "message": "All model attempts failed"}

    async def _payload_attempts(
        self,
        payload: dict[str, Any],
        fallback_models: list[str] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        models = [payload.get("model"), *(fallback_models or [])]
        seen: set[str] = set()
        for model in models:
            if not model or model in seen:
                continue
            seen.add(model)
            yield {**payload, "model": model}

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(url, headers=self._headers(), json={**payload, "stream": False})
            if response.status_code >= 400:
                message = response.text
                if response.status_code in {404, 429, 502, 503, 504}:
                    return {"_retryable_model_error": True, "status_code": response.status_code, "message": message}
                raise HTTPException(status_code=response.status_code, detail=message)
            return response.json()

    async def _stream_one_attempt(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        request_payload = {**payload, "stream": True}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=self._headers(), json=request_payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    message = body.decode("utf-8", errors="replace")
                    if response.status_code in {404, 429, 502, 503, 504}:
                        yield {"event": "retry_model", "message": message, "status_code": response.status_code}
                        return
                    yield {"event": "error", "message": message, "status_code": response.status_code}
                    yield {"event": "done", "ok": False, "model_used": payload.get("model")}
                    return

                final_model = payload.get("model")
                final_provider = None
                final_usage = None
                final_chunk = None

                async for raw_line in response.aiter_lines():
                    if not raw_line:
                        continue
                    if raw_line.startswith(":"):
                        yield {"event": "keepalive", "message": raw_line.removeprefix(':').strip()}
                        continue
                    if not raw_line.startswith("data:"):
                        continue

                    data = raw_line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        yield {
                            "event": "done",
                            "ok": True,
                            "model_used": final_model,
                            "provider": final_provider,
                            "usage": final_usage,
                            "raw_final_chunk": final_chunk,
                        }
                        return

                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        yield {"event": "raw", "data": data}
                        continue

                    if "error" in chunk:
                        yield {
                            "event": "error",
                            "message": chunk["error"].get("message", "OpenRouter stream error"),
                            "error": chunk["error"],
                            "model_used": final_model,
                        }
                        yield {"event": "done", "ok": False, "model_used": final_model}
                        return

                    final_chunk = chunk
                    final_model = chunk.get("model") or final_model
                    final_provider = chunk.get("provider") or final_provider
                    final_usage = chunk.get("usage") or final_usage

                    for choice in chunk.get("choices", []):
                        delta = choice.get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield {
                                "event": "token",
                                "content": content,
                                "model_used": final_model,
                                "provider": final_provider,
                            }

                yield {
                    "event": "done",
                    "ok": True,
                    "model_used": final_model,
                    "provider": final_provider,
                    "usage": final_usage,
                    "raw_final_chunk": final_chunk,
                }
