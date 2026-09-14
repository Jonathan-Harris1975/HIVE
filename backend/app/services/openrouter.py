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
from app.services.context_resilience import (
    add_openrouter_context_compression,
    deterministic_compact_payload,
    provider_order,
)
from app.services.headroom_optimizer import HeadroomOptimizer, HeadroomOutcome

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
        self._headroom = HeadroomOptimizer(settings)

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

    async def chat_completion(
        self,
        payload: dict[str, Any],
        fallback_models: list[str] | None = None,
        *,
        allow_implicit_free_fallback: bool = True,
    ) -> dict[str, Any]:
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
        if allow_implicit_free_fallback:
            candidate_payloads = [
                item async for item in self._payload_attempts(payload, fallback_models)
            ]
        else:
            candidate_payloads = [
                item
                async for item in self._payload_attempts(
                    payload,
                    fallback_models,
                    allow_implicit_free_fallback=False,
                )
            ]

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
        *,
        allow_implicit_free_fallback: bool = True,
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

        if (
            not ordered_models
            and allow_implicit_free_fallback
            and self.settings.openrouter_free_fallback_model
        ):
            ordered_models = [self.settings.openrouter_free_fallback_model]

        max_attempts = 1 + max(0, int(self.settings.openrouter_max_fallback_attempts))
        for model in ordered_models[:max_attempts]:
            candidate = {**payload, "model": model}
            yield candidate

    async def _context_attempts(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[tuple[str, str, dict[str, str], dict[str, Any]]]:
        """Yield configured context routes in failover order.

        Proxy routes are attempted only when a base URL is configured.  Internal
        routing metadata is removed before anything leaves HIVE.
        """

        profile = str(payload.get("_hive_context_profile") or "general").strip().lower()
        clean_payload = dict(payload)
        clean_payload.pop("_hive_context_profile", None)
        model = clean_payload.get("model")
        raw_messages = clean_payload.get("messages")
        messages: list[dict[str, str]] | None = None
        if isinstance(raw_messages, list) and all(
            isinstance(raw, dict)
            and isinstance(raw.get("role"), str)
            and isinstance(raw.get("content"), str)
            for raw in raw_messages
        ):
            messages = [
                {"role": str(raw["role"]), "content": str(raw["content"])}
                for raw in raw_messages
            ]
        exact_context = bool(messages and self._requires_exact_context(messages))

        if profile == "coding":
            primary = self.settings.context_coding_primary_provider
            fallbacks = self.settings.context_coding_fallback_providers
        else:
            primary = self.settings.context_primary_provider
            fallbacks = self.settings.context_fallback_providers

        openrouter_url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        openrouter_headers = self._headers()
        for route_name in provider_order(primary, fallbacks):
            if route_name == "leanctx":
                base = self.settings.leanctx_base_url.strip().rstrip("/")
                if not base:
                    continue
                yield (
                    route_name,
                    f"{base}/chat/completions",
                    self._proxy_headers(self.settings.leanctx_api_key, openrouter_headers),
                    dict(clean_payload),
                )
                continue

            if route_name == "context_gateway":
                base = self.settings.context_gateway_base_url.strip().rstrip("/")
                if not base:
                    continue
                yield (
                    route_name,
                    f"{base}/chat/completions",
                    self._proxy_headers(
                        self.settings.context_gateway_api_key, openrouter_headers
                    ),
                    dict(clean_payload),
                )
                continue

            if route_name == "headroom":
                if messages is None or not isinstance(model, str) or not model:
                    continue
                outcome = await asyncio.to_thread(
                    self._headroom.optimise,
                    messages,
                    model=model,
                    exact_context=exact_context,
                )
                self._log_headroom(outcome, model=model)
                if outcome.failed or (
                    outcome.skipped_reason and outcome.skipped_reason != "exact_context"
                ):
                    logger.warning(
                        "Headroom did not produce a usable context result reason=%s; trying next route",
                        outcome.skipped_reason or "failed",
                    )
                    continue
                yield (
                    route_name,
                    openrouter_url,
                    openrouter_headers,
                    {**clean_payload, "messages": outcome.messages},
                )
                continue

            if route_name == "openrouter":
                if exact_context:
                    continue
                yield (
                    route_name,
                    openrouter_url,
                    openrouter_headers,
                    add_openrouter_context_compression(clean_payload),
                )
                continue

            if route_name == "deterministic":
                if exact_context:
                    continue
                yield (
                    route_name,
                    openrouter_url,
                    openrouter_headers,
                    deterministic_compact_payload(
                        clean_payload,
                        max_chars=self.settings.context_local_max_chars,
                        exact_context=False,
                    ),
                )
                continue

            if route_name == "direct":
                yield route_name, openrouter_url, openrouter_headers, dict(clean_payload)

    @staticmethod
    def _proxy_headers(
        proxy_api_key: str, openrouter_headers: dict[str, str]
    ) -> dict[str, str]:
        headers = dict(openrouter_headers)
        key = str(proxy_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @staticmethod
    def _requires_exact_context(messages: list[dict[str, str]]) -> bool:
        """Bypass compression where HIVE must retain exact source-file context."""
        exact_markers = (
            "Answer using the attached file content only where relevant.",
            "AnchorPatch/v1",
        )
        return any(
            message.get("role") == "system"
            and any(marker in message.get("content", "") for marker in exact_markers)
            for message in messages
        )

    def _log_headroom(self, outcome: HeadroomOutcome, *, model: str) -> None:
        if outcome.failed:
            logger.warning(
                "Headroom fail-open model=%s reason=%s", model, outcome.skipped_reason or "unknown"
            )
            return
        if outcome.skipped_reason and outcome.skipped_reason not in {"disabled", "exact_context"}:
            logger.debug("Headroom skipped model=%s reason=%s", model, outcome.skipped_reason)
        if self.settings.headroom_log_savings and outcome.tokens_saved > 0:
            logger.info(
                "Headroom saved=%d tokens model=%s before=%d after=%d transforms=%s",
                outcome.tokens_saved,
                model,
                outcome.tokens_before,
                outcome.tokens_after,
                ",".join(outcome.transforms) or "unknown",
            )

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
        last_failure: dict[str, Any] | None = None
        async for route_name, url, headers, route_payload in self._context_attempts(payload):
            try:
                async with httpx.AsyncClient(timeout=self._attempt_timeout()) as client:
                    response = await client.post(
                        url, headers=headers, json={**route_payload, "stream": False}
                    )
            except httpx.TimeoutException as exc:
                logger.info(
                    "Context route timed out route=%s model=%s error=%s",
                    route_name,
                    payload.get("model"),
                    exc,
                )
                last_failure = {
                    "_retryable_model_error": True,
                    "status_code": 408,
                    "message": f"Context route {route_name} timed out for {payload.get('model')}: {exc}",
                }
                if route_name in {"leanctx", "context_gateway"}:
                    continue
                return last_failure
            except httpx.HTTPError as exc:
                logger.info(
                    "Context route failed route=%s model=%s error=%s",
                    route_name,
                    payload.get("model"),
                    exc,
                )
                last_failure = {
                    "_retryable_model_error": True,
                    "status_code": 502,
                    "message": f"Context route {route_name} failed for {payload.get('model')}: {exc}",
                }
                if route_name in {"leanctx", "context_gateway"}:
                    continue
                return last_failure

            if response.status_code >= 400:
                message = response.text
                last_failure = {
                    "_retryable_model_error": True,
                    "status_code": response.status_code,
                    "message": message,
                }
                if route_name in {"leanctx", "context_gateway"}:
                    logger.warning(
                        "Context proxy route failed route=%s status=%s; trying fallback",
                        route_name,
                        response.status_code,
                    )
                    continue
                if route_name in {"openrouter", "deterministic"} and self._is_context_limit_response(
                    response.status_code, message
                ):
                    logger.warning(
                        "Context route could not fit prompt route=%s; trying fallback",
                        route_name,
                    )
                    continue
                if response.status_code in RETRYABLE_STATUS_CODES:
                    return last_failure
                raise HTTPException(status_code=response.status_code, detail=message)

            data = response.json()
            if isinstance(data, dict):
                data.setdefault("hive_context_route", route_name)
            return data

        return last_failure or {
            "_retryable_model_error": True,
            "status_code": 503,
            "message": "No configured context route was available.",
        }

    @staticmethod
    def _is_context_limit_response(status_code: int | None, message: str) -> bool:
        if status_code not in {400, 413, 422}:
            return False
        text = str(message or "").lower()
        markers = (
            "context length",
            "context_length",
            "maximum context",
            "too many messages",
            "prompt is too long",
            "input is too long",
            "token limit",
        )
        return any(marker in text for marker in markers)

    async def _stream_one_attempt(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        last_retry: dict[str, Any] | None = None
        async for route_name, url, headers, route_payload in self._context_attempts(payload):
            saw_token = False
            route_fallback = False
            async for event in self._stream_route_attempt(
                route_payload, url=url, headers=headers
            ):
                event_type = event.get("event")
                if event_type == "token":
                    saw_token = True
                    yield event
                    continue

                if event_type in {"retry_model", "error"} and not saw_token:
                    status_code = event.get("status_code")
                    message = str(event.get("message") or "")
                    can_context_fallback = route_name in {"leanctx", "context_gateway"}
                    if route_name in {"openrouter", "deterministic"}:
                        can_context_fallback = self._is_context_limit_response(
                            int(status_code) if isinstance(status_code, int) else None,
                            message,
                        )
                    if can_context_fallback:
                        last_retry = {
                            "event": "retry_model",
                            "message": message or f"Context route {route_name} failed",
                            "status_code": status_code,
                            "model_used": payload.get("model"),
                        }
                        logger.warning(
                            "Streaming context route failed route=%s; trying fallback",
                            route_name,
                        )
                        route_fallback = True
                        break

                if event_type == "done":
                    event = {**event, "context_route": route_name}
                yield event

            if route_fallback:
                continue
            return

        yield last_retry or {
            "event": "retry_model",
            "message": "No configured context route was available.",
            "status_code": 503,
            "model_used": payload.get("model"),
        }

    async def _stream_route_attempt(
        self,
        payload: dict[str, Any],
        *,
        url: str,
        headers: dict[str, str],
    ) -> AsyncIterator[dict[str, Any]]:
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
                async with client.stream("POST", url, headers=headers, json=request_payload) as response:
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
