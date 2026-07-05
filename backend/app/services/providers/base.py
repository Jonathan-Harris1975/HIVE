from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

# Phase 4 - Provider Framework.
#
# A small adapter abstraction so no provider-specific code needs to exist
# outside `app/services/providers/*_provider.py`. Every adapter exposes the
# same shape (available models, pricing, context, tool support, structured
# output support, health, latency) regardless of upstream API differences.
# `OpenRouterCompatibleProvider` (openrouter_provider.py) covers both the
# primary OpenRouter provider and any future OpenRouter-compatible provider
# purely through configuration (base URL + API key), so adding a new
# compatible provider never requires new adapter code.


@dataclass(frozen=True)
class ProviderModelInfo:
    model_id: str
    name: str
    context_length: int | None
    pricing_prompt: float | None
    pricing_completion: float | None
    supports_tools: bool
    supports_structured_output: bool
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    raw: dict[str, Any]


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    ok: bool
    latency_ms: float | None
    model_count: int | None
    error: str | None


@runtime_checkable
class ProviderAdapter(Protocol):
    """Every provider adapter implements this shape."""

    name: str

    async def list_models(self, *, force_refresh: bool = False) -> list[ProviderModelInfo]: ...

    async def health(self) -> ProviderHealth: ...


def _parse_price(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item) for item in value if item)
    return ()


def parse_provider_model(raw: dict[str, Any]) -> ProviderModelInfo:
    """Normalise a raw OpenRouter-shaped model payload into ProviderModelInfo.

    Shared by every OpenRouter-compatible adapter so capability parsing
    (tool support, structured output support) lives in exactly one place.
    """
    model_id = str(raw.get("id") or "")
    name = str(raw.get("name") or model_id)
    pricing = raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}
    supported_parameters = _string_tuple(raw.get("supported_parameters"))
    architecture = raw.get("architecture") if isinstance(raw.get("architecture"), dict) else {}

    return ProviderModelInfo(
        model_id=model_id,
        name=name,
        context_length=_safe_int(raw.get("context_length")),
        pricing_prompt=_parse_price(pricing.get("prompt")),
        pricing_completion=_parse_price(pricing.get("completion")),
        supports_tools=any(
            token in supported_parameters for token in ("tools", "tool_choice")
        ),
        supports_structured_output=any(
            token in supported_parameters for token in ("response_format", "structured_outputs")
        ),
        input_modalities=_string_tuple(architecture.get("input_modalities")),
        output_modalities=_string_tuple(architecture.get("output_modalities")),
        raw=raw,
    )


def _safe_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


async def time_call(coro) -> tuple[Any, float]:
    """Await `coro` and return (result, elapsed_ms)."""
    start = time.perf_counter()
    result = await coro
    elapsed_ms = (time.perf_counter() - start) * 1000
    return result, elapsed_ms
