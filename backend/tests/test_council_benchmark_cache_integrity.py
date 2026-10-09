"""Cached benchmark snapshots must not silently drop malformed rows."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.ai_council import _load_benchmark_snapshot


def test_malformed_benchmark_cache_is_rejected():
    store = SimpleNamespace(
        list_metadata=lambda **kwargs: {
            "ok": True,
            "items": [
                {
                    "source_type": "benchmark_snapshot",
                    "source_id": "openrouter:artificial-analysis",
                    "metadata": {
                        "items": [{"model": "valid"}, "corrupted"],
                        "fetched_at": datetime.now(UTC).isoformat(),
                    },
                }
            ],
        }
    )
    rows, status = _load_benchmark_snapshot(
        store,
        provider_name="openrouter",
        source="artificial-analysis",
        max_age_days=7,
    )
    assert rows == []
    assert status["ok"] is False
    assert "malformed" in status["reason"]
