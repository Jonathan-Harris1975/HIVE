"""Service-authenticated, bounded inline Headroom compression for AIMS."""
from __future__ import annotations

import asyncio
import json
import secrets
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.services.headroom_optimizer import HeadroomOptimizer

router = APIRouter(tags=["headroom"])
_bearer = HTTPBearer(auto_error=False)
_capacity = threading.BoundedSemaphore(1)
_MAX_BODY = 1_500_000


async def require_aims_compression_key(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
                                       settings: Settings = Depends(get_settings)) -> None:
    expected = settings.aims_api_key.strip()
    if not expected or expected.startswith("{{"):
        raise HTTPException(status_code=503, detail="Compression service authentication is not configured")
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Compression service bearer token required")
    if not secrets.compare_digest(credentials.credentials.strip(), expected):
        raise HTTPException(status_code=403, detail="Invalid compression service token")


def _optimise(settings: Settings, messages: list[dict[str, Any]], model: str) -> dict[str, Any]:
    # The worker thread owns capacity until it really finishes, even if its caller disconnects.
    if not _capacity.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Compression capacity is busy")
    try:
        outcome = HeadroomOptimizer(settings).optimise(messages, model=model)
        if outcome.failed or outcome.skipped_reason == "disabled":
            raise HTTPException(status_code=503, detail="Inline Headroom is unavailable")
        candidate = outcome.messages
        protected_changed = any(
            before.get("role") in {"system", "developer"} and before.get("content") != after.get("content")
            for before, after in zip(messages, candidate)
        )
        if protected_changed or candidate[-1].get("content") != messages[-1].get("content"):
            raise HTTPException(status_code=503, detail="Compression changed protected instructions")
        compressed = (outcome.compressed and outcome.tokens_saved > 0
                      and 0 <= outcome.tokens_after < outcome.tokens_before)
        output = [{**original, "content": transformed["content"]} for original, transformed in zip(messages, candidate)] if compressed else messages
        return {"messages": output, "compression_skipped": not compressed,
                "skip_reason": outcome.skipped_reason or (None if compressed else "no-verified-saving"),
                "tokens_before": outcome.tokens_before, "tokens_after": outcome.tokens_after if compressed else outcome.tokens_before,
                "tokens_saved": outcome.tokens_saved if compressed else 0,
                "compression_ratio": outcome.tokens_after / outcome.tokens_before if compressed else 1,
                "transforms_applied": list(outcome.transforms)}
    finally:
        _capacity.release()


@router.post("/compress", dependencies=[Depends(require_aims_compression_key)])
async def compress(request: Request, response: Response, settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY:
            raise HTTPException(status_code=413, detail="Compression request is too large")
        body.extend(chunk)
    try:
        payload = json.loads(body)
        messages, model = payload["messages"], payload["model"]
        if not isinstance(model, str) or not 1 <= len(model) <= 200 or not isinstance(messages, list) or not 1 <= len(messages) <= 64:
            raise ValueError("Invalid message/model shape")
        if any(not isinstance(item, dict) or item.get("role") not in {"system", "developer", "user", "assistant", "tool"}
               or not isinstance(item.get("content"), str) for item in messages):
            raise ValueError("Text message contract required")
        if sum(len(item["content"]) for item in messages) > min(settings.headroom_max_input_chars, 262_144):
            raise HTTPException(status_code=413, detail="Compression context exceeds the supported limit")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=422, detail="Invalid compression request") from None
    # HIVE governs compression settings; callers cannot weaken instruction protection or enable ML downloads.
    return await asyncio.to_thread(_optimise, settings, messages, model)
