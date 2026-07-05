from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import Settings
from app.services.openrouter import OpenRouterClient
from app.services.providers.base import ProviderHealth, ProviderModelInfo, parse_provider_model


class OpenRouterProvider:
    """Primary provider adapter. Wraps the existing OpenRouterClient rather
    than re-implementing its caching/retry/model-list behaviour, so this
    adapter adds a uniform capability surface without duplicating logic."""

    name = "openrouter"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = OpenRouterClient(settings)

    async def list_models(self, *, force_refresh: bool = False) -> list[ProviderModelInfo]:
        raw_models = await self._client.list_models(force_refresh=force_refresh)
        return [parse_provider_model(model) for model in raw_models]

    async def health(self) -> ProviderHealth:
        start = time.perf_counter()
        try:
            models = await self.list_models(force_refresh=True)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProviderHealth(
                provider=self.name, ok=True, latency_ms=elapsed_ms, model_count=len(models), error=None
            )
        except Exception as error:  # noqa: BLE001 - health probes must never raise
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProviderHealth(
                provider=self.name, ok=False, latency_ms=elapsed_ms, model_count=None, error=str(error)
            )


class OpenRouterCompatibleProvider:
    """Generic adapter for any future provider exposing an OpenRouter-shaped
    `/models` endpoint (id, pricing, context_length, supported_parameters,
    architecture). Adding a new compatible provider only requires a name,
    base_url and api_token via configuration; no new adapter code."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    async def list_models(self, *, force_refresh: bool = False) -> list[ProviderModelInfo]:
        async with httpx.AsyncClient(
            timeout=max(1.0, self.timeout_seconds), transport=self._transport
        ) as client:
            response = await client.get(f"{self.base_url}/models", headers=self._headers)
            response.raise_for_status()
            payload: Any = response.json()
        raw_models = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw_models, list):
            return []
        return [parse_provider_model(model) for model in raw_models if isinstance(model, dict)]

    async def health(self) -> ProviderHealth:
        start = time.perf_counter()
        try:
            models = await self.list_models(force_refresh=True)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProviderHealth(
                provider=self.name, ok=True, latency_ms=elapsed_ms, model_count=len(models), error=None
            )
        except Exception as error:  # noqa: BLE001 - health probes must never raise
            elapsed_ms = (time.perf_counter() - start) * 1000
            return ProviderHealth(
                provider=self.name, ok=False, latency_ms=elapsed_ms, model_count=None, error=str(error)
            )
