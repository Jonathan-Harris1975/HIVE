from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.context_resilience import (
    add_openrouter_context_compression,
    deterministic_compact_payload,
    provider_order,
)
from app.services.openrouter import OpenRouterClient


SYNTHETIC_OPENROUTER_TOKEN = "test-openrouter-token"


def test_provider_order_is_deduplicated_and_direct_is_break_glass() -> None:
    assert provider_order("context_gateway", "headroom,openrouter,direct") == (
        "context_gateway",
        "headroom",
        "openrouter",
        "direct",
    )
    assert provider_order("leanctx", "leanctx,context_gateway") == (
        "leanctx",
        "context_gateway",
        "direct",
    )


@pytest.mark.asyncio
async def test_general_and_coding_primary_routes_are_independently_configurable() -> None:
    settings = Settings(
        OPENROUTER_API_KEY=SYNTHETIC_OPENROUTER_TOKEN,
        HIVE_CONTEXT_GATEWAY_BASE_URL="http://context-gateway:8080/v1",
        HIVE_CONTEXT_GATEWAY_API_KEY="gateway-token",
        HIVE_LEANCTX_BASE_URL="http://leanctx:4444/v1",
        HIVE_LEANCTX_API_KEY="lean-token",
    )
    client = OpenRouterClient(settings)
    base = {"model": "test/model", "messages": [{"role": "user", "content": "hello"}]}

    general = [item async for item in client._context_attempts({**base, "_hive_context_profile": "general"})]
    coding = [item async for item in client._context_attempts({**base, "_hive_context_profile": "coding"})]

    assert general[0][0] == "context_gateway"
    assert general[0][1] == "http://context-gateway:8080/v1/chat/completions"
    assert general[0][2]["Authorization"] == "Bearer gateway-token"
    assert "_hive_context_profile" not in general[0][3]

    assert coding[0][0] == "leanctx"
    assert coding[0][1] == "http://leanctx:4444/v1/chat/completions"
    assert coding[0][2]["Authorization"] == "Bearer lean-token"
    assert "_hive_context_profile" not in coding[0][3]


@pytest.mark.asyncio
async def test_primary_can_be_changed_entirely_by_environment_setting() -> None:
    settings = Settings(
        OPENROUTER_API_KEY=SYNTHETIC_OPENROUTER_TOKEN,
        HIVE_CONTEXT_PRIMARY_PROVIDER="openrouter",
        HIVE_CONTEXT_FALLBACK_PROVIDERS="deterministic,direct",
    )
    client = OpenRouterClient(settings)
    payload = {"model": "test/model", "messages": [{"role": "user", "content": "hello"}]}
    routes = [item async for item in client._context_attempts(payload)]
    assert routes[0][0] == "openrouter"
    assert routes[0][2]["Authorization"] == f"Bearer {SYNTHETIC_OPENROUTER_TOKEN}"
    assert routes[0][3]["plugins"] == [{"id": "context-compression"}]


def test_local_compactor_preserves_system_and_latest_turn() -> None:
    payload = {
        "messages": [
            {"role": "system", "content": "rules"},
            {"role": "assistant", "content": "x" * 20_000},
            {"role": "user", "content": "latest"},
        ]
    }
    compacted = deterministic_compact_payload(payload, max_chars=9_000)
    assert compacted["messages"][0]["content"] == "rules"
    assert compacted["messages"][-1]["content"] == "latest"
    assert "locally compacted" in compacted["messages"][1]["content"]


def test_openrouter_context_plugin_is_idempotent() -> None:
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    assert add_openrouter_context_compression(
        add_openrouter_context_compression(payload)
    )["plugins"] == [{"id": "context-compression"}]
