from __future__ import annotations

import json
import re
import threading
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.governed_repositories import DEFAULT_GITHUB_SOURCES
from app.services.autonomous_recovery_coordinator import RecoveryCoordinator
from app.services.autonomous_recovery_signals import correlate_failure
from app.storage.r2 import R2Storage
from app.storage.d1 import D1MetadataStore

_ALLOWED_SEVERITIES = {"info", "warning", "critical"}
_SECRET_KEY = re.compile(r"(authorization|bearer|credential|password|secret|token|api[_-]?key|access[_-]?key)", re.I)
_MEMORY: deque[dict[str, Any]] = deque()
_LOCK = threading.Lock()


class OpsEventPersistenceError(RuntimeError):
    """Raised when an enabled authoritative store cannot accept an event."""


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
    # Preserve provider failure outcomes when the envelope omits status.\n    # Do not infer failure from severity or arbitrary free-text messages.\n    status = _text(payload.get("status") or payload.get("conclusion") or "open", limit=40).lower()
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
        try:
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
        except Exception as exc:  # the caller must retry with the same event ID
            raise OpsEventPersistenceError("Operational event was not persisted.") from exc
        if not persisted:
            raise OpsEventPersistenceError("Operational event was not persisted.")
    # Incident registration is observation-only. Never grant an execution lease
    # from an externally supplied event, even when the ingestion token is valid.
    if persisted and settings.recovery_signal_ingest_enabled:
        coordinator = RecoveryCoordinator(store, R2Storage(settings), settings.cf_r2_bucket)
        result = correlate_failure(
            event, coordinator,
            allowed_repositories=frozenset(DEFAULT_GITHUB_SOURCES.values()),
        )
        if result["reason"] == "storage_unavailable":
            raise OpsEventPersistenceError("Recovery incident registration failed.")
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
    store = D1MetadataStore(settings)
    d1_available = store.enabled
    d1_error: str | None = None
    if store.enabled:
        # D1 is authoritative when enabled. Falling back to process-local memory
        # can resurrect events deleted by another worker.
        items: list[dict[str, Any]] = []
        result = store.list_metadata(lane="ops_events", limit=max(safe_limit, 100))
        if result.get("ok"):
            rows = result.get("items")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        metadata = row.get("metadata")
                        if isinstance(metadata, dict):
                            items.append(metadata)
        else:
            d1_error = _text(result.get("message") or result.get("error") or "D1 query failed", limit=200)
    else:
        items = _memory_events()

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
        # An enabled D1 store is authoritative. An outage must not look like an
        # empty inbox, because that could hide an unresolved incident.
        "ok": d1_error is None,
        "available": d1_error is None,
        "enabled": settings.ops_event_ingest_enabled,
        "persistent_store": d1_available,
        "persistent_store_error": d1_error,
        "count": min(len(filtered), safe_limit),
        "items": filtered[:safe_limit],
    }



def delete_ops_event(settings: Settings, event_id: str) -> dict[str, Any]:
    """Delete one operational event from memory and the persistent ops-events lane."""
    clean_id = _text(event_id, limit=160)
    if not clean_id:
        raise ValueError("An event ID is required.")

    store = D1MetadataStore(settings)
    if store.enabled:
        result = store.delete_metadata_ids([f"ops-event:{clean_id}"])
        if not result.get("ok"):
            raise RuntimeError("The operational event could not be deleted from D1.")

    with _LOCK:
        retained = [item for item in _MEMORY if item.get("event_id") != clean_id]
        _MEMORY.clear()
        _MEMORY.extend(retained)
    return {"ok": True, "event_id": clean_id}


def delete_all_ops_events(settings: Settings) -> dict[str, Any]:
    """Delete every operational event from memory and the persistent ops-events lane."""
    store = D1MetadataStore(settings)
    if store.enabled:
        result = store.query(
            "DELETE FROM hive_ecosystem_metadata WHERE lane = ?",
            ["ops_events"],
        )
        if not result.get("ok"):
            raise RuntimeError("Operational events could not be deleted from D1.")

    with _LOCK:
        deleted_from_memory = len(_MEMORY)
        _MEMORY.clear()
    return {"ok": True, "deleted_from_memory": deleted_from_memory}

def clear_ops_events_for_tests() -> None:
    with _LOCK:
        _MEMORY.clear()
