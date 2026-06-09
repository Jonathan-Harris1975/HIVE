from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings


RETRYABLE_STATUS_CODES = {404, 408, 409, 425, 429, 500, 502, 503, 504}


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
        self._model_ids_cache: tuple[float, set[str]] | None = None
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

    def _attempt_timeout(self) -> httpx.Timeout:
        seconds = max(1.0, float(self.settings.openrouter_attempt_timeout_seconds))
        connect = min(10.0, seconds)
        return httpx.Timeout(timeout=seconds, connect=connect)

    async def validate_key(self) -> bool:
        await self.list_models(force_refresh=True)
        return True

    async def list_models(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        now = time.time()
        if not force_refresh and self._models_cache and now - self._models_cache[0] < self._cache_seconds:
            return self._models_cache[1]

        url = f"{self.settings.openrouter_base_url.rstrip('/')}/models"
        timeout = max(1.0, float(self.settings.openrouter_model_list_timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            payload = response.json()
            models = payload.get("data", []) if isinstance(payload, dict) else []
            self._models_cache = (now, models)
            self._model_ids_cache = (now, {model["id"] for model in models if isinstance(model, dict) and model.get("id")})
            return models

    async def model_ids(self) -> set[str]:
        now = time.time()
        if self._model_ids_cache and now - self._model_ids_cache[0] < self._cache_seconds:
            return self._model_ids_cache[1]
        models = await self.list_models()
        return {model["id"] for model in models if isinstance(model, dict) and model.get("id")}

    async def chat_completion(self, payload: dict[str, Any], fallback_models: list[str] | None = None) -> dict[str, Any]:
        """Non-streaming chat completion for Make.com and simple smoke tests.

        The attempt list is preflighted against OpenRouter's current model list by
        default. That prevents known-dead IDs from burning the full request timeout
        before falling back to the configured free model. If every attempt still
        fails, return a structured failure payload instead of surfacing a naked 502;
        that makes Koyeb/ReqBin diagnostics usable rather than opaque.

        Some reasoning-heavy free models can spend a very small ``max_tokens`` budget
        on hidden reasoning and return no visible assistant text. HIVE treats that
        as incomplete rather than a clean success, then tries the configured fallback
        ladder before returning a clear ``empty_model_reply`` diagnostic.
        """

        attempts: list[dict[str, Any]] = []
        candidate_payloads = [item async for item in self._payload_attempts(payload, fallback_models)]

        for candidate_payload in candidate_payloads:
            model = candidate_payload.get("model")
            response_payload = await self._post_json(candidate_payload)
            if response_payload.get("_retryable_model_error"):
                attempts.append(
                    {
                        "model": model,
                        "status_code": response_payload.get("status_code"),
                        "message": response_payload.get("message"),
                    }
                )
                continue

            if self.settings.openrouter_empty_reply_retry_enabled and self._has_empty_visible_reply(response_payload):
                attempts.append(
                    {
                        "model": model,
                        "status_code": 204,
                        "message": "Model returned no visible assistant text.",
                        "finish_reason": self._first_finish_reason(response_payload),
                        "empty_reply": True,
                    }
                )
                continue

            if attempts:
                response_payload.setdefault("hive_attempts", attempts + [{"model": model, "ok": True}])
            return response_payload

        empty_reply_seen = any(attempt.get("empty_reply") for attempt in attempts)
        failure_message = (
            "OpenRouter returned no visible assistant text for the selected model and configured fallbacks."
            if empty_reply_seen
            else "OpenRouter request failed for the selected model and all configured fallback models."
        )
        return {
            "_all_attempts_failed": True,
            "_empty_model_reply": empty_reply_seen,
            "hive_error_code": "empty_model_reply" if empty_reply_seen else "openrouter_attempts_failed",
            "model": attempts[-1].get("model") if attempts else None,
            "provider": None,
            "usage": None,
            "hive_attempts": attempts,
            "choices": [
                {
                    "message": {"content": failure_message},
                    "finish_reason": "empty_reply" if empty_reply_seen else "error",
                }
            ],
        }

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
                    if event.get("ok") is False:
                        yield {
                            "event": "done",
                            "ok": False,
                            "model_used": event.get("model_used") or state.model_used,
                            "message": event.get("message"),
                        }
                        return
                    state = OpenRouterStreamState(
                        model_used=event.get("model_used") or state.model_used,
                        provider=event.get("provider"),
                        usage=event.get("usage"),
                        raw_final_chunk=event.get("raw_final_chunk"),
                    )
                    if self.settings.openrouter_empty_reply_retry_enabled and not saw_token:
                        yield {
                            "event": "meta",
                            "type": "empty_reply_retry",
                            "from_model": state.model_used,
                            "message": "Model returned no visible assistant text; trying fallback if available.",
                        }
                        break
                    yield {
                        "event": "done",
                        "ok": True,
                        "model_used": state.model_used,
                        "provider": state.provider,
                        "usage": state.usage,
                    }
                    return

                yield event
        yield {"event": "done", "ok": False, "message": "All model attempts failed or returned no visible assistant text"}

    def _has_empty_visible_reply(self, response_payload: dict[str, Any]) -> bool:
        choices = response_payload.get("choices") or []
        if not choices:
            return True
        message = (choices[0] or {}).get("message") or {}
        return not self._visible_text(message.get("content")).strip()

    def _first_finish_reason(self, response_payload: dict[str, Any]) -> str | None:
        choices = response_payload.get("choices") or []
        if not choices:
            return None
        return (choices[0] or {}).get("finish_reason")

    def _visible_text(self, content: Any) -> str:
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

    async def _payload_attempts(
        self,
        payload: dict[str, Any],
        fallback_models: list[str] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        models = [payload.get("model"), *(fallback_models or [])]
        seen: set[str] = set()
        ordered_models: list[str] = []
        for model in models:
            if not isinstance(model, str) or not model or model in seen:
                continue
            seen.add(model)
            ordered_models.append(model)

        valid_model_ids = await self._safe_model_ids_for_preflight()
        if valid_model_ids is not None and ordered_models:
            # Preflight should protect us from obviously dead user/requested models,
            # but it must not aggressively filter controlled fallback aliases.
            # OpenRouter can resolve some aliases to dated endpoint IDs at runtime;
            # exact-ID filtering of fallbacks can therefore remove every safe route.
            primary_model = ordered_models[0]
            if primary_model not in valid_model_ids and not self._is_controlled_fallback_alias(primary_model):
                ordered_models = ordered_models[1:]

        # In free-only mode, a known-invalid paid/non-free primary should not burn a
        # request attempt before the free fallback. This keeps bad-model smoke tests
        # fast and stops accidental paid fallback drift.
        if (
            not self.settings.allow_paid_fallback
            and ordered_models
            and ordered_models[0] != self.settings.openrouter_free_fallback_model
            and not ordered_models[0].endswith(":free")
            and self.settings.openrouter_free_fallback_model in ordered_models[1:]
        ):
            ordered_models = [self.settings.openrouter_free_fallback_model] + [
                model for model in ordered_models[1:] if model != self.settings.openrouter_free_fallback_model
            ]

        if not ordered_models and self.settings.openrouter_free_fallback_model:
            ordered_models = [self.settings.openrouter_free_fallback_model]

        max_attempts = 1 + max(0, int(self.settings.openrouter_max_fallback_attempts))
        for model in ordered_models[:max_attempts]:
            yield {**payload, "model": model}


    def _is_controlled_fallback_alias(self, model: str) -> bool:
        """Allow configured free/router aliases even when the model list returns dated IDs."""

        return model == self.settings.openrouter_free_fallback_model or model.endswith(":free") or model.startswith("~")

    async def _safe_model_ids_for_preflight(self) -> set[str] | None:
        if not self.settings.openrouter_model_preflight_enabled:
            return None
        try:
            return await self.model_ids()
        except (httpx.HTTPError, ValueError):
            # Model-list preflight is a speed optimisation, not a reason to block chat.
            return None

    async def _post_json(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self._attempt_timeout()) as client:
                response = await client.post(url, headers=self._headers(), json={**payload, "stream": False})
        except httpx.TimeoutException as exc:
            return {
                "_retryable_model_error": True,
                "status_code": 408,
                "message": f"OpenRouter attempt timed out for {payload.get('model')}: {exc}",
            }
        except httpx.HTTPError as exc:
            return {
                "_retryable_model_error": True,
                "status_code": 502,
                "message": f"OpenRouter request failed for {payload.get('model')}: {exc}",
            }

        if response.status_code >= 400:
            message = response.text
            if response.status_code in RETRYABLE_STATUS_CODES:
                return {"_retryable_model_error": True, "status_code": response.status_code, "message": message}
            raise HTTPException(status_code=response.status_code, detail=message)
        return response.json()

    async def _stream_one_attempt(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        request_payload = {**payload, "stream": True}

        # For streaming, keep the read timeout open, but still bound connect/write/pool
        # failures so a dead endpoint does not pin the worker indefinitely.
        attempt_seconds = max(1.0, float(self.settings.openrouter_attempt_timeout_seconds))
        timeout = httpx.Timeout(timeout=None, connect=min(10.0, attempt_seconds), write=attempt_seconds, pool=attempt_seconds)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=self._headers(), json=request_payload) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        message = body.decode("utf-8", errors="replace")
                        if response.status_code in RETRYABLE_STATUS_CODES:
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
        except httpx.TimeoutException as exc:
            yield {
                "event": "retry_model",
                "message": f"OpenRouter stream attempt timed out for {payload.get('model')}: {exc}",
                "status_code": 408,
            }
        except httpx.HTTPError as exc:
            yield {
                "event": "retry_model",
                "message": f"OpenRouter stream attempt failed for {payload.get('model')}: {exc}",
                "status_code": 502,
            }
