from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import HTTPException, status

from app.core.config import Settings


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

    async def stream_chat_completion(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"
        payload = {**payload, "stream": True}

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, headers=self._headers(), json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise HTTPException(status_code=response.status_code, detail=body.decode("utf-8"))

                async for raw_line in response.aiter_lines():
                    if not raw_line:
                        continue
                    if raw_line.startswith(":"):
                        yield {"event": "keepalive", "data": raw_line}
                        continue
                    if not raw_line.startswith("data:"):
                        continue
                    data = raw_line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        yield {"event": "done", "ok": True}
                        return
                    yield {"event": "token", "data": data}
