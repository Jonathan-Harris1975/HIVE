from __future__ import annotations

import json
import re
import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.storage.d1 import D1MetadataStore

_ALLOWED_SEVERITIES = {"info", "warning", "critical"}
_SECRET_KEY = re.compile(r"(authorization|bearer|credential|password|secret|token|api[_-]?key|access[_-]?key)", re.I)
_MEMORY: deque[dict[str, Any]] = deque()
_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: object, *, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    return clean[:limit]


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 3:
        return "[depth-limited]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value, limit=1000)
    if isinstance(value, list):
        return [_safe_value(item, depth=depth + 1) for item in value[:30]]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            clean_key = _text(key, limit=80)
            result[clean_key] = "[redacted]" if _SECRET_KEY.search(clean_key) else _safe_value(item, depth=depth + 1)
        return result
    return _text(value, limit=1000)


def normalise_ops_event(payload: dict[str, Any]) -> dict[str, Any]:
    severity = _text(payload.get("severity") or "warning", limit=16).lower()
    if severity not in _ALLOWED_SEVERITIES:
        severity = "warning"
    event_id = _text(payload.get("event_id") or payload.get("id"), limit=160) or str(uuid.uuid4())
    occurred_at = _text(payload.get("occurred_at") or payload.get("time"), limit=64) or _now_iso()
    source = _text(payload.get("source") or payload.get("provider") or "unknown", limit=80)
    service = _text(payload.get("service") or payload.get("repo") or "unknown", limit=80)
    event_type = _text(payload.get("event_type") or payload.get("type") or "operational_event", limit=100)
    title = _text(payload.get("title") or f"{service} {event_type}", limit=180)
    summary = _text(payload.get("summary") or payload.get("message") or title, limit=1200)
    status = _text(payload.get("status") or "open", limit=40).lower()
    release_id = _text(payload.get("release_id") or payload.get("commit_sha"), limit=160) or None
    url = _text(payload.get("url") or payload.get("details_url"), limit=1000) or None
    details = _safe_value(payload.get("details") or {})
    event = {
        "event_id": event_id,
        "source": source,
        "service": service,
        "environment": _text(payload.get("environment") or "production", limit=40),
        "severity": severity,
        "event_type": event_type,
        "title": title,
        "summary": summary,
        "status": status,
        "release_id": release_id,
        "occurred_at": occurred_at,
        "received_at": _now_iso(),
        "url": url,
        "details": details,
    }
    # Bound the complete event before storing it or returning it to HIVE-UI.
    encoded = json.dumps(event, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > 16_384:
        event["details"] = {"truncated": True}
        event["summary"] = event["summary"][:600]
    return event


def ingest_ops_event(settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    event = normalise_ops_event(payload)
    with _LOCK:
        # Replace a matching event ID to keep provider retries idempotent.
        existing = [item for item in _MEMORY if item.get("event_id") != event["event_id"]]
        existing.insert(0, event)
        _MEMORY.clear()
        _MEMORY.extend(existing[: max(10, settings.ops_event_memory_limit)])

    persisted = False
    store = D1MetadataStore(settings)
    if store.enabled:
        result = store.upsert_metadata(
            item_id=f"ops-event:{event['event_id']}",
            lane="ops_events",
            source_type=event["event_type"],
            source_id=event["service"],
            title=event["title"],
            url=event["url"],
            metadata=event,
        )
        persisted = bool(result.get("ok"))
    return {"ok": True, "accepted": True, "persisted": persisted, "event": event}


def _memory_events() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(item) for item in _MEMORY]


def list_ops_events(
    settings: Settings,
    *,
    limit: int = 50,
    severity: str | None = None,
    service: str | None = None,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 200))
    items = _memory_events()
    store = D1MetadataStore(settings)
    d1_available = store.enabled
    d1_error: str | None = None
    if store.enabled:
        result = store.list_metadata(lane="ops_events", limit=max(safe_limit, 100))
        if result.get("ok"):
            for row in result.get("items", []):
                metadata = row.get("metadata")
                if isinstance(metadata, dict):
                    items.append(metadata)
        else:
            d1_error = _text(result.get("message") or result.get("error") or "D1 query failed", limit=200)

    deduped: dict[str, dict[str, Any]] = {}
    for item in items:
        event_id = _text(item.get("event_id"), limit=160)
        if event_id:
            deduped[event_id] = item
    filtered = list(deduped.values())
    if severity:
        wanted = severity.strip().lower()
        filtered = [item for item in filtered if item.get("severity") == wanted]
    if service:
        wanted_service = service.strip().lower()
        filtered = [item for item in filtered if str(item.get("service", "")).lower() == wanted_service]
    filtered.sort(key=lambda item: str(item.get("occurred_at") or item.get("received_at") or ""), reverse=True)
    return {
        "ok": True,
        "enabled": settings.ops_event_ingest_enabled,
        "persistent_store": d1_available,
        "persistent_store_error": d1_error,
        "count": min(len(filtered), safe_limit),
        "items": filtered[:safe_limit],
    }


def clear_ops_events_for_tests() -> None:
    with _LOCK:
        _MEMORY.clear()
