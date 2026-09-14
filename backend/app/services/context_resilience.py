"""Context-provider selection and safe fallback transforms for HIVE.

LeanCTX and Context Gateway are treated as external OpenAI-compatible proxies.
HIVE only needs their API roots/tokens, so either service can disappear without
becoming an application dependency.  In-process Headroom, OpenRouter's native
context-compression plugin, a deterministic local compactor and raw OpenRouter
complete the failover chain.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

CONTEXT_PROVIDERS = frozenset(
    {"leanctx", "context_gateway", "headroom", "openrouter", "deterministic", "direct"}
)


def provider_order(primary: str, fallbacks: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(fallbacks, str):
        candidates = [item.strip().lower() for item in fallbacks.split(",")]
    else:
        candidates = [str(item).strip().lower() for item in fallbacks]

    ordered: list[str] = []
    for item in [str(primary).strip().lower(), *candidates]:
        if item in CONTEXT_PROVIDERS and item not in ordered:
            ordered.append(item)
    if "direct" not in ordered:
        ordered.append("direct")
    return tuple(ordered)


def add_openrouter_context_compression(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate = deepcopy(dict(payload))
    raw_plugins = candidate.get("plugins")
    plugins = list(raw_plugins) if isinstance(raw_plugins, list) else []
    if not any(
        isinstance(item, Mapping) and item.get("id") == "context-compression"
        for item in plugins
    ):
        plugins.append({"id": "context-compression"})
    candidate["plugins"] = plugins
    return candidate


def deterministic_compact_payload(
    payload: Mapping[str, Any], *, max_chars: int, exact_context: bool = False
) -> dict[str, Any]:
    """Dependency-free last-resort compaction, preserving system/latest turns."""

    candidate = deepcopy(dict(payload))
    messages = candidate.get("messages")
    if exact_context or not isinstance(messages, list) or max_chars <= 0:
        return candidate
    if not all(
        isinstance(message, Mapping)
        and isinstance(message.get("role"), str)
        and isinstance(message.get("content"), str)
        for message in messages
    ):
        return candidate

    copied = [dict(message) for message in messages]
    total = sum(len(str(message.get("content", ""))) for message in copied)
    if total <= max_chars:
        return candidate

    protected: set[int] = {
        index for index, message in enumerate(copied) if message.get("role") == "system"
    }
    if copied:
        protected.add(len(copied) - 1)

    protected_chars = sum(len(str(copied[index].get("content", ""))) for index in protected)
    shrinkable = [index for index in range(len(copied)) if index not in protected]
    if not shrinkable:
        return candidate

    available = max(0, max_chars - protected_chars)
    per_message = max(256, available // len(shrinkable)) if available else 256
    marker = "\n...[locally compacted for context budget]...\n"
    for index in shrinkable:
        content = str(copied[index].get("content", ""))
        if len(content) <= per_message:
            continue
        keep = max(64, (per_message - len(marker)) // 2)
        copied[index]["content"] = f"{content[:keep]}{marker}{content[-keep:]}"

    candidate["messages"] = copied
    return candidate
