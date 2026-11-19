"""Recover confirmed OpenRouter usage for streams interrupted by a client."""
from __future__ import annotations

import logging
import math
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.storage.sql_store import SqlStore

logger = logging.getLogger("uvicorn.error.hive.chat_usage")


def reconcile_conversation_usage(
    settings: Settings, store: SqlStore, conversation_id: str, messages: list[dict[str, Any]],
) -> bool:
    """Try a bounded number of lookups; unavailable evidence stays unknown for the next read."""
    if not settings.openrouter_api_key:
        return False
    # The caller's message list is display-limited. Query SQL separately so
    # old unresolved turns remain eligible for the rotating recovery batch.
    eligible_messages = {
        str(message["id"]): message for message in messages if message.get("id")
    }
    eligible_messages.update({
        str(message["id"]): message
        for message in store.unresolved_interrupted_usage(conversation_id)
        if message.get("id")
    })
    candidates = []
    for message in eligible_messages.values():
        metadata = message.get("metadata")
        if (message.get("role") == "assistant" and message.get("cost_usd") is None
                and isinstance(metadata, dict) and metadata.get("stream_ended_early")
                and isinstance(metadata.get("generation_id"), str)
                and metadata["generation_id"].startswith("gen-") and message.get("id")):
            candidates.append((message, metadata["generation_id"]))
    if not candidates:
        return False
    changed = False
    batch_size = 3
    batch_count = (len(candidates) + batch_size - 1) // batch_size
    batch = int(time.time() // 3) % batch_count
    start = batch * batch_size
    ordered_candidates = candidates[start:] + candidates[:start]
    for message, generation_id in ordered_candidates[:batch_size]:
        try:
            response = httpx.get(
                f"{settings.openrouter_base_url.rstrip('/')}/generation",
                params={"id": generation_id},
                headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                timeout=3.0,
            )
            response.raise_for_status()
            data = response.json().get("data")
            if not isinstance(data, dict) or data.get("id") != generation_id:
                continue
            if not data.get("cancelled") and not data.get("finish_reason"):
                continue
            cost = data.get("total_cost")
            prompt = data.get("tokens_prompt")
            completion = data.get("tokens_completion")
            if (isinstance(cost, bool) or not isinstance(cost, (int, float))
                    or not math.isfinite(cost) or cost < 0
                    or isinstance(prompt, bool) or not isinstance(prompt, int) or prompt < 0
                    or isinstance(completion, bool) or not isinstance(completion, int) or completion < 0):
                continue
            usage = {"prompt_tokens": prompt, "completion_tokens": completion,
                     "total_tokens": prompt + completion, "cost": cost,
                     "generation_id": generation_id, "source": "openrouter_generation"}
            result = store.reconcile_interrupted_usage(
                conversation_id=conversation_id, message_id=str(message["id"]), usage=usage,
                model=data.get("model"), provider=data.get("provider_name"),
            )
            changed = changed or bool(result.get("ok"))
            if not result.get("ok"):
                logger.warning("Failed to save recovered chat usage: %s", result.get("error"))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            logger.info("OpenRouter generation usage is not available yet")
    return changed
