from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings

logger = logging.getLogger("uvicorn.error.hive.openrouter")


RETRYABLE_STATUS_CODES = {400, 404, 408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class OpenRouterStreamState:
    model_used: str | None = None
    provider: str | None = None
    usage: dict[str, Any] | None = None
    raw_final_chunk: dict[str, Any] | None = None


class OpenRouterClient:
    _shared_models_cache: ClassVar[dict[str, tuple[float, list[dict[str, Any]]]]] = {}
    _shared_model_ids_cache: ClassVar[dict[str, tuple[float, set[str]]]] = {}
    _shared_cache_locks: ClassVar[dict[str, asyncio.Lock]] = {}
    _cache_seconds: ClassVar[int] = 10 * 60

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _cache_key(self) -> str:
        key_digest = hashlib.sha256(self.settings.openrouter_api_key.encode("utf-8")).hexdigest()[:16]
        return f"{self.settings.openrouter_base_url.rstrip('/')}:{key_digest}"

    @classmethod
    def _lock_for_cache_key(cls, cache_key: str) -> asyncio.Lock:
        lock = cls._shared_cache_locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            cls._shared_cache_locks[cache_key] = lock
        return lock

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
        cache_key = self._cache_key()
        cached = self._shared_models_cache.get(cache_key)
        if not force_refresh and cached and now - cached[0] < self._cache_seconds:
            return cached[1]

        async with self._lock_for_cache_key(cache_key):
            now = time.time()
            cached = self._shared_models_cache.get(cache_key)
            if not force_refresh and cached and now - cached[0] < self._cache_seconds:
                return cached[1]

            url = f"{self.settings.openrouter_base_url.rstrip('/')}/models"
            timeout = max(1.0, float(self.settings.openrouter_model_list_timeout_seconds))
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(
                    url,
                    headers=self._headers(),
                    params={"output_modalities": "all"},
                )
                response.raise_for_status()
                payload = response.json()
                models = payload.get("data", []) if isinstance(payload, dict) else []
                model_ids = {model["id"] for model in models if isinstance(model, dict) and model.get("id")}
                self._shared_models_cache[cache_key] = (now, models)
                self._shared_model_ids_cache[cache_key] = (now, model_ids)
                return models

    async def model_ids(self) -> set[str]:
        now = time.time()
        cache_key = self._cache_key()
        cached = self._shared_model_ids_cache.get(cache_key)
        if cached and now - cached[0] < self._cache_seconds:
            return cached[1]
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
            yield {
                "event": "meta",
                "type": "model_attempt",
                "model_used": state.model_used,
                "message": f"Trying {state.model_used}",
            }

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
                        "model_used": candidate_payload.get("model"),
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
                            "provider": event.get("provider"),
                            "usage": event.get("usage"),
                            "message": event.get("message"),
                            "finish_reason": event.get("finish_reason"),
                            "completion_truncated": bool(event.get("completion_truncated")),
                            "partial_response": bool(event.get("partial_response")),
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
                        "finish_reason": event.get("finish_reason"),
                        "completion_truncated": bool(event.get("completion_truncated")),
                    }
                    return

                yield event
        yield {
            "event": "done",
            "ok": False,
            "message": "All model attempts failed or returned no visible assistant text",
            "finish_reason": "all_attempts_failed",
            "completion_truncated": False,
        }

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
            logger.info("OpenRouter attempt timed out model=%s error=%s", payload.get("model"), exc)
            return {
                "_retryable_model_error": True,
                "status_code": 408,
                "message": f"OpenRouter attempt timed out for {payload.get('model')}: {exc}",
            }
        except httpx.HTTPError as exc:
            logger.info("OpenRouter attempt failed model=%s error=%s", payload.get("model"), exc)
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
        # stream_options.include_usage is required for OpenRouter/OpenAI-compatible
        # streaming to emit a usage object on the final chunk at all; without it,
        # `final_usage` below always stays None regardless of the usage.include flag.
        request_payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}

        # Bound connection failures without cutting off healthy providers that pause
        # briefly while composing. The previous 6s read window was fast, but it could
        # guillotine a valid answer mid-sentence on mobile/Koyeb routes.
        attempt_seconds = max(1.0, float(self.settings.openrouter_attempt_timeout_seconds))
        idle_seconds = max(1.0, float(self.settings.openrouter_stream_idle_timeout_seconds))
        first_token_seconds = max(1.0, float(self.settings.openrouter_stream_first_token_timeout_seconds))
        timeout = httpx.Timeout(
            timeout=None,
            connect=min(8.0, attempt_seconds),
            read=idle_seconds,
            write=attempt_seconds,
            pool=attempt_seconds,
        )
        final_model = payload.get("model")
        final_provider = None
        final_usage = None
        final_chunk = None
        final_finish_reason = None
        attempt_started = time.perf_counter()
        saw_visible_token = False

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

                    async for raw_line in response.aiter_lines():
                        if not raw_line:
                            continue
                        if (
                            not saw_visible_token
                            and time.perf_counter() - attempt_started > first_token_seconds
                        ):
                            yield {
                                "event": "retry_model",
                                "message": (
                                    f"OpenRouter stream produced no visible tokens within "
                                    f"{first_token_seconds:.1f}s for {payload.get('model')}"
                                ),
                                "status_code": 408,
                                "model_used": payload.get("model"),
                            }
                            return
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
                                "finish_reason": final_finish_reason,
                                "completion_truncated": final_finish_reason == "length",
                                "raw_final_chunk": final_chunk,
                            }
                            return

                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            yield {"event": "raw", "data": data}
                            continue

                        if "error" in chunk:
                            error_payload = chunk["error"] if isinstance(chunk.get("error"), dict) else {}
                            yield {
                                "event": "retry_model",
                                "message": error_payload.get("message", "OpenRouter stream error"),
                                "error": error_payload or chunk.get("error"),
                                "model_used": final_model,
                            }
                            return

                        final_chunk = chunk
                        final_model = chunk.get("model") or final_model
                        final_provider = chunk.get("provider") or final_provider
                        final_usage = chunk.get("usage") or final_usage

                        for choice in chunk.get("choices", []):
                            finish_reason = choice.get("finish_reason")
                            if finish_reason:
                                final_finish_reason = finish_reason
                            delta = choice.get("delta") or {}
                            content = delta.get("content")
                            if content:
                                saw_visible_token = True
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
                        "finish_reason": final_finish_reason,
                        "completion_truncated": final_finish_reason == "length",
                        "raw_final_chunk": final_chunk,
                    }
        except httpx.TimeoutException as exc:
            logger.info(
                "OpenRouter stream timed out model=%s saw_visible_token=%s error=%s",
                payload.get("model"),
                saw_visible_token,
                exc,
            )
            if saw_visible_token:
                yield {
                    "event": "done",
                    "ok": False,
                    "message": f"OpenRouter stream paused for more than {idle_seconds:.1f}s before finishing.",
                    "model_used": final_model,
                    "provider": final_provider,
                    "usage": final_usage,
                    "finish_reason": "stream_timeout",
                    "completion_truncated": True,
                    "partial_response": True,
                }
                return
            yield {
                "event": "retry_model",
                "message": f"OpenRouter stream timed out for {payload.get('model')}: {exc}",
                "status_code": 408,
                "model_used": payload.get("model"),
            }
        except httpx.HTTPError as exc:
            logger.info("OpenRouter stream attempt failed model=%s error=%s", payload.get("model"), exc)
            yield {
                "event": "retry_model",
                "message": f"OpenRouter stream attempt failed for {payload.get('model')}: {exc}",
                "status_code": 502,
            }
