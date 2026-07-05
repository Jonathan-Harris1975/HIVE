from __future__ import annotations

import json
from typing import Any

from app.core.config import Settings
from app.services.providers.base import ProviderAdapter
from app.services.providers.openrouter_provider import (
    OpenRouterCompatibleProvider,
    OpenRouterProvider,
)

# Phase 4 - Provider Framework.
#
# discover_providers() is the single place that knows how many providers are
# configured. Everything else (Phase 5 AI Council, Phase 6 Benchmark Engine)
# only depends on the ProviderAdapter shape from base.py, so adding a new
# OpenRouter-compatible provider is a configuration change here, never a new
# code path elsewhere.


def discover_providers(settings: Settings) -> list[ProviderAdapter]:
    providers: list[ProviderAdapter] = []

    if settings.openrouter_api_key:
        providers.append(OpenRouterProvider(settings))

    for extra in _parse_extra_providers(settings.provider_framework_extra_providers_json):
        providers.append(
            OpenRouterCompatibleProvider(
                name=extra["name"],
                base_url=extra["base_url"],
                api_token=extra.get("api_token", ""),
                timeout_seconds=float(extra.get("timeout_seconds", 15.0)),
            )
        )
    return providers


def _parse_extra_providers(seed_json: str) -> list[dict[str, Any]]:
    if not seed_json or not seed_json.strip():
        return []
    try:
        data = json.loads(seed_json)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    valid: list[dict[str, Any]] = []
    for entry in data:
        if isinstance(entry, dict) and entry.get("name") and entry.get("base_url"):
            valid.append(entry)
    return valid


async def provider_health_report(settings: Settings) -> dict[str, Any]:
    providers = discover_providers(settings)
    reports = [await provider.health() for provider in providers]
    return {
        "provider_count": len(providers),
        "providers": [
            {
                "provider": report.provider,
                "ok": report.ok,
                "latency_ms": report.latency_ms,
                "model_count": report.model_count,
                "error": report.error,
            }
            for report in reports
        ],
    }
