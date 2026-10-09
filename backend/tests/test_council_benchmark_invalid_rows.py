"""Reject malformed benchmark rows rather than silently trusting partial evidence."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import ai_council


@pytest.mark.asyncio
async def test_malformed_provider_rows_fail_closed(monkeypatch):
    provider = SimpleNamespace(
        name="openrouter",
        list_benchmarks=AsyncMock(return_value=[{"model": "valid"}, "bad-row"]),
    )
    settings = SimpleNamespace(
        ai_council_benchmark_attempts=1,
        ai_council_benchmark_retry_base_seconds=0,
        ai_council_benchmark_cache_max_age_days=1,
    )
    monkeypatch.setattr(
        ai_council, "_load_benchmark_snapshot",
        lambda *args, **kwargs: ([], {"ok": False, "reason": "no cache"}),
    )
    monkeypatch.setattr(
        ai_council, "_store_benchmark_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("invalid rows must not be cached")),
    )
    models, status = await ai_council._load_provider_benchmarks(settings, object(), provider)
    assert models == {}
    assert status["ok"] is False
    assert status["mode"] == "unavailable"
    assert "malformed" in status["error"]
