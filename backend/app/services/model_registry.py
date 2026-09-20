from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.storage.d1 import D1MetadataStore

logger = logging.getLogger("uvicorn.error.hive.model_registry")

# Phase 3 - Model Registry.
#
# A dynamic, in-process registry of ranked models per category. Categories
# are fixed (coding, reasoning, planning, vision, research, fast, cheap,
# creative, long_context); the *models* within each category are entirely
# configuration/runtime driven, not hard-coded. The highest-ranked model in
# a category is that category's default.
#
# Persistence: the registry remains an in-memory cache for fast reads on the
# request path. When D1 is configured, mutations are mirrored to the existing
# hive_ecosystem_metadata table. If D1 is temporarily unavailable, the accepted
# mutation is written to a small local reconciliation journal before it is made
# visible in memory. On process restart the journal is overlaid on any D1 state,
# preventing a failed delete from silently resurrecting and preserving failed
# registrations until D1 recovers. Reconciliation is idempotent and serialised.

LANE = "model_registry"
DEFAULT_RECONCILIATION_PATH = Path("local-data/model-registry-pending.json")

CATEGORIES: tuple[str, ...] = (
    "coding",
    "reasoning",
    "planning",
    "vision",
    "research",
    "fast",
    "cheap",
    "creative",
    "long_context",
)

_LOCK = threading.RLock()
_RECONCILE_LOCK = threading.Lock()
_REGISTRY: dict[str, list["RankedModel"]] = {category: [] for category in CATEGORIES}
_PENDING: dict[str, "PendingOperation"] = {}
_PERSISTENCE_STATE: dict[str, dict[str, object]] = {}
_ACTIVE_JOURNAL_PATH: Path | None = None
_METRICS: dict[str, int] = {
    "persistence_attempts": 0,
    "persistence_successes": 0,
    "persistence_failures": 0,
    "reconciliation_attempts": 0,
    "reconciliation_successes": 0,
    "reconciliation_failures": 0,
}


class ModelRegistryError(ValueError):
    pass


class ModelRegistryPersistenceError(ModelRegistryError):
    """Raised only when a failed D1 mutation cannot be journalled safely."""


#: Confidence levels for a model's benchmark/latency/cost figures, mirroring
#: the confidence convention already used by Repository Council
#: (app/services/repository_council.py): "measured" means the figure came
#: from an actual benchmark run or provider-reported metric, "heuristic"
#: means it was estimated/derived, and "unverified" (the default) means no
#: real signal has been attached yet and the figure should be treated as an
#: unverified ranking hint only.
CONFIDENCE_LEVELS: tuple[str, ...] = ("measured", "heuristic", "unverified")
LIFECYCLE_STATUSES: tuple[str, ...] = (
    "active",
    "watch",
    "deprecating",
    "quarantined",
    "retired",
)
ROUTABLE_LIFECYCLE_STATUSES = frozenset({"active", "watch"})


@dataclass(frozen=True)
class RankedModel:
    model_id: str
    category: str
    score: float
    provider: str | None
    notes: str | None
    registered_at: float
    benchmark_score: float | None = None
    confidence: str = "unverified"
    latency_ms: float | None = None
    cost_per_1k_tokens: float | None = None
    canonical_slug: str | None = None
    expiration_date: str | None = None
    lifecycle_status: str = "active"


@dataclass(frozen=True)
class PendingOperation:
    operation_id: str
    action: str
    category: str
    model_id: str
    queued_at: float
    model: dict[str, object] | None = None
    attempts: int = 0
    last_error: str | None = None

    @property
    def key(self) -> str:
        return _item_id(self.category, self.model_id)


# ---------- validation / serialisation helpers ----------


def _require_known_category(category: str) -> None:
    if category not in CATEGORIES:
        raise ModelRegistryError(
            f"Unknown model category: {category!r}. Expected one of {CATEGORIES}."
        )


def _item_id(category: str, model_id: str) -> str:
    return f"model-registry:{category}:{model_id}"


def _optional_float(metadata: dict[str, Any], key: str) -> float | None:
    value = metadata.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ranked_from_mapping(data: dict[str, object]) -> RankedModel | None:
    category = str(data.get("category") or "")
    model_id = str(data.get("model_id") or "")
    if category not in CATEGORIES or not model_id:
        return None
    confidence = str(data.get("confidence") or "unverified")
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "unverified"
    lifecycle = str(data.get("lifecycle_status") or "active")
    if lifecycle not in LIFECYCLE_STATUSES:
        lifecycle = "active"
    score_raw: Any = data.get("score") or 0.0
    try:
        score = float(score_raw)
    except (TypeError, ValueError):
        score = 0.0

    registered_at_raw: Any = data.get("registered_at") or time.time()
    try:
        registered_at = float(registered_at_raw)
    except (TypeError, ValueError):
        registered_at = time.time()
    return RankedModel(
        model_id=model_id,
        category=category,
        score=score,
        provider=str(data["provider"]) if data.get("provider") is not None else None,
        notes=str(data["notes"]) if data.get("notes") is not None else None,
        registered_at=registered_at,
        benchmark_score=_optional_float(data, "benchmark_score"),
        confidence=confidence,
        latency_ms=_optional_float(data, "latency_ms"),
        cost_per_1k_tokens=_optional_float(data, "cost_per_1k_tokens"),
        canonical_slug=(
            str(data["canonical_slug"]) if data.get("canonical_slug") is not None else None
        ),
        expiration_date=(
            str(data["expiration_date"]) if data.get("expiration_date") is not None else None
        ),
        lifecycle_status=lifecycle,
    )


def _metadata_for_ranked(ranked: RankedModel) -> dict[str, object]:
    return asdict(ranked)


# ---------- reconciliation journal ----------


def _journal_path(store: "D1MetadataStore | None") -> Path:
    settings = getattr(store, "settings", None)
    configured = getattr(settings, "model_registry_reconciliation_path", "")
    path = Path(str(configured).strip()) if str(configured or "").strip() else DEFAULT_RECONCILIATION_PATH
    return path


def _set_active_journal_path(path: Path) -> None:
    global _ACTIVE_JOURNAL_PATH
    _ACTIVE_JOURNAL_PATH = path


def _write_pending_journal_locked(path: Path) -> None:
    payload = {
        "version": 1,
        "pending": [asdict(item) for item in sorted(_PENDING.values(), key=lambda op: op.queued_at)],
    }
    try:
        if not _PENDING:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temp_path.write_text(json.dumps(payload, sort_keys=True, ensure_ascii=False), encoding="utf-8")
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temp_path, path)
    except OSError as exc:
        logger.error(
            "model_registry_journal_write_failed path=%s error=%s",
            path,
            exc,
            extra={
                "event": "model_registry_journal_write_failed",
                "path": str(path),
                "error": str(exc),
            },
        )
        raise ModelRegistryPersistenceError(
            f"Model Registry persistence failed and reconciliation journal could not be written: {exc}"
        ) from exc


def _load_pending_journal(path: Path) -> int:
    _set_active_journal_path(path)
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "model_registry_journal_load_failed path=%s error=%s",
            path,
            exc,
            extra={
                "event": "model_registry_journal_load_failed",
                "path": str(path),
                "error": str(exc),
            },
        )
        return 0

    pending = raw.get("pending") if isinstance(raw, dict) else None
    if not isinstance(pending, list):
        logger.error(
            "model_registry_journal_invalid path=%s",
            path,
            extra={"event": "model_registry_journal_invalid", "path": str(path)},
        )
        return 0

    loaded = 0
    with _LOCK:
        for item in pending:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "")
            category = str(item.get("category") or "")
            model_id = str(item.get("model_id") or "")
            operation_id = str(item.get("operation_id") or "")
            if action not in {"upsert", "delete"} or category not in CATEGORIES or not model_id:
                continue
            try:
                queued_at = float(item.get("queued_at") or time.time())
                attempts = int(item.get("attempts") or 0)
            except (TypeError, ValueError):
                continue
            operation = PendingOperation(
                operation_id=operation_id or uuid.uuid4().hex,
                action=action,
                category=category,
                model_id=model_id,
                queued_at=queued_at,
                model=item.get("model") if isinstance(item.get("model"), dict) else None,
                attempts=max(0, attempts),
                last_error=(
                    str(item["last_error"]) if item.get("last_error") is not None else None
                ),
            )
            _PENDING[operation.key] = operation
            _PERSISTENCE_STATE[operation.key] = {
                "state": "pending",
                "action": operation.action,
                "error": operation.last_error,
                "updated_at": operation.queued_at,
            }
            loaded += 1
    if loaded:
        logger.warning(
            "model_registry_pending_restored count=%s path=%s",
            loaded,
            path,
            extra={
                "event": "model_registry_pending_restored",
                "count": loaded,
                "path": str(path),
            },
        )
    return loaded


def _record_pending(
    *,
    store: "D1MetadataStore | None",
    action: str,
    category: str,
    model_id: str,
    model: RankedModel | None,
    error: str | None,
) -> PendingOperation:
    """Atomically journal the latest desired mutation before touching D1.

    Replacing an older pending operation for the same model prevents a stale
    reconciliation retry from winning after a newer user action. If the journal
    cannot be written, process state is rolled back and the mutation is rejected
    before it can be exposed in memory.
    """
    operation = PendingOperation(
        operation_id=uuid.uuid4().hex,
        action=action,
        category=category,
        model_id=model_id,
        queued_at=time.time(),
        model=_metadata_for_ranked(model) if model is not None else None,
        attempts=0,
        last_error=error,
    )
    path = _journal_path(store)
    _set_active_journal_path(path)
    with _LOCK:
        previous_pending = _PENDING.get(operation.key)
        previous_state = _PERSISTENCE_STATE.get(operation.key)
        _PENDING[operation.key] = operation
        _PERSISTENCE_STATE[operation.key] = {
            "state": "pending",
            "action": action,
            "error": error,
            "updated_at": operation.queued_at,
        }
        try:
            _write_pending_journal_locked(path)
        except ModelRegistryPersistenceError:
            if previous_pending is None:
                _PENDING.pop(operation.key, None)
            else:
                _PENDING[operation.key] = previous_pending
            if previous_state is None:
                _PERSISTENCE_STATE.pop(operation.key, None)
            else:
                _PERSISTENCE_STATE[operation.key] = previous_state
            raise
    return operation


def _clear_pending_if_current(operation: PendingOperation, path: Path) -> bool:
    with _LOCK:
        current = _PENDING.get(operation.key)
        if current is None or current.operation_id != operation.operation_id:
            return False
        _PENDING.pop(operation.key, None)
        _PERSISTENCE_STATE[operation.key] = {
            "state": "durable",
            "action": operation.action,
            "error": None,
            "updated_at": time.time(),
        }
        try:
            _write_pending_journal_locked(path)
        except ModelRegistryPersistenceError as exc:
            # D1 already accepted this exact operation. Keep the same operation
            # pending so a later idempotent reconciliation can safely repeat the
            # durable write and clean up the journal.
            restored = replace(
                operation,
                last_error=f"D1 durable; reconciliation journal cleanup failed: {exc}",
            )
            _PENDING[operation.key] = restored
            _PERSISTENCE_STATE[operation.key] = {
                "state": "pending",
                "action": operation.action,
                "error": restored.last_error,
                "updated_at": time.time(),
            }
            return False
        return True


def _update_pending_failure(operation: PendingOperation, path: Path, error: str) -> None:
    with _LOCK:
        current = _PENDING.get(operation.key)
        if current is None or current.operation_id != operation.operation_id:
            return
        updated = replace(current, attempts=current.attempts + 1, last_error=error)
        _PENDING[operation.key] = updated
        _PERSISTENCE_STATE[operation.key] = {
            "state": "pending",
            "action": updated.action,
            "error": error,
            "updated_at": time.time(),
        }
        try:
            _write_pending_journal_locked(path)
        except ModelRegistryPersistenceError:
            # The pre-write journal already contains this same desired operation,
            # so recovery remains safe. Keep the richer failure detail in memory
            # even when the diagnostic update cannot be flushed to disk.
            return


# ---------- D1 persistence ----------


def _store_enabled(store: "D1MetadataStore | None") -> bool:
    return bool(store is not None and getattr(store, "enabled", False))


def _bump_metric(name: str) -> None:
    with _LOCK:
        _METRICS[name] = _METRICS.get(name, 0) + 1


def _result_error(result: object) -> str:
    if isinstance(result, dict):
        for key in ("error", "message", "detail"):
            value = result.get(key)
            if value:
                return str(value)
        return json.dumps(result, sort_keys=True, default=str)[:1000]
    return str(result)[:1000]


def _persist_model_now(store: "D1MetadataStore", ranked: RankedModel) -> tuple[bool, str | None]:
    _bump_metric("persistence_attempts")
    try:
        result = store.upsert_metadata(
            item_id=_item_id(ranked.category, ranked.model_id),
            lane=LANE,
            source_type=ranked.category,
            source_id=ranked.model_id,
            title=f"{ranked.category}:{ranked.model_id}",
            url=None,
            metadata=_metadata_for_ranked(ranked),
        )
    except Exception as exc:  # noqa: BLE001 - converted into a recoverable pending operation
        error = f"{type(exc).__name__}: {exc}"
        _bump_metric("persistence_failures")
        logger.error(
            "model_registry_persistence_failed action=upsert category=%s model_id=%s error=%s",
            ranked.category,
            ranked.model_id,
            error,
            extra={
                "event": "model_registry_persistence_failed",
                "action": "upsert",
                "category": ranked.category,
                "model_id": ranked.model_id,
                "error": error,
            },
        )
        return False, error
    if not isinstance(result, dict) or not result.get("ok"):
        error = _result_error(result)
        _bump_metric("persistence_failures")
        logger.error(
            "model_registry_persistence_failed action=upsert category=%s model_id=%s error=%s",
            ranked.category,
            ranked.model_id,
            error,
            extra={
                "event": "model_registry_persistence_failed",
                "action": "upsert",
                "category": ranked.category,
                "model_id": ranked.model_id,
                "error": error,
            },
        )
        return False, error
    _bump_metric("persistence_successes")
    return True, None


def _delete_persisted_model_now(
    store: "D1MetadataStore", category: str, model_id: str
) -> tuple[bool, str | None]:
    _bump_metric("persistence_attempts")
    try:
        result = store.delete_metadata_ids([_item_id(category, model_id)])
    except Exception as exc:  # noqa: BLE001 - converted into a recoverable pending operation
        error = f"{type(exc).__name__}: {exc}"
        _bump_metric("persistence_failures")
        logger.error(
            "model_registry_persistence_failed action=delete category=%s model_id=%s error=%s",
            category,
            model_id,
            error,
            extra={
                "event": "model_registry_persistence_failed",
                "action": "delete",
                "category": category,
                "model_id": model_id,
                "error": error,
            },
        )
        return False, error
    if not isinstance(result, dict) or not result.get("ok"):
        error = _result_error(result)
        _bump_metric("persistence_failures")
        logger.error(
            "model_registry_persistence_failed action=delete category=%s model_id=%s error=%s",
            category,
            model_id,
            error,
            extra={
                "event": "model_registry_persistence_failed",
                "action": "delete",
                "category": category,
                "model_id": model_id,
                "error": error,
            },
        )
        return False, error
    _bump_metric("persistence_successes")
    return True, None


def _set_not_configured_state(category: str, model_id: str, action: str) -> None:
    with _LOCK:
        _PERSISTENCE_STATE[_item_id(category, model_id)] = {
            "state": "not_configured",
            "action": action,
            "error": None,
            "updated_at": time.time(),
        }


# ---------- public mutation API ----------


def register_model(
    category: str,
    model_id: str,
    *,
    score: float,
    provider: str | None = None,
    notes: str | None = None,
    benchmark_score: float | None = None,
    confidence: str = "unverified",
    latency_ms: float | None = None,
    cost_per_1k_tokens: float | None = None,
    canonical_slug: str | None = None,
    expiration_date: str | None = None,
    lifecycle_status: str = "active",
    store: "D1MetadataStore | None" = None,
) -> list[RankedModel]:
    """Register or re-score a model and mirror the mutation durably when configured.

    D1 failure remains fail-open for runtime availability, but it is never silent:
    the mutation is journalled as pending before it becomes visible in memory.
    """
    _require_known_category(category)
    if not model_id:
        raise ModelRegistryError("model_id is required")
    if confidence not in CONFIDENCE_LEVELS:
        raise ModelRegistryError(
            f"Unknown confidence level: {confidence!r}. Expected one of {CONFIDENCE_LEVELS}."
        )
    if lifecycle_status not in LIFECYCLE_STATUSES:
        raise ModelRegistryError(
            f"Unknown lifecycle status: {lifecycle_status!r}. Expected one of {LIFECYCLE_STATUSES}."
        )

    ranked = RankedModel(
        model_id=model_id,
        category=category,
        score=float(score),
        provider=provider,
        notes=notes,
        registered_at=time.time(),
        benchmark_score=benchmark_score,
        confidence=confidence,
        latency_ms=latency_ms,
        cost_per_1k_tokens=cost_per_1k_tokens,
        canonical_slug=canonical_slug,
        expiration_date=expiration_date,
        lifecycle_status=lifecycle_status,
    )

    if _store_enabled(store):
        assert store is not None
        pending = _record_pending(
            store=store,
            action="upsert",
            category=category,
            model_id=model_id,
            model=ranked,
            error=None,
        )
        path = _journal_path(store)
        persisted, error = _persist_model_now(store, ranked)
        if persisted:
            _clear_pending_if_current(pending, path)
        else:
            _update_pending_failure(
                pending,
                path,
                error or "unknown D1 persistence failure",
            )
    else:
        _set_not_configured_state(category, model_id, "upsert")

    with _LOCK:
        existing = [m for m in _REGISTRY[category] if m.model_id != model_id]
        existing.append(ranked)
        existing.sort(key=lambda m: m.score, reverse=True)
        _REGISTRY[category] = existing
        return list(existing)


def remove_model(category: str, model_id: str, *, store: "D1MetadataStore | None" = None) -> bool:
    _require_known_category(category)
    with _LOCK:
        exists = any(m.model_id == model_id for m in _REGISTRY[category])
    if not exists:
        return False

    if _store_enabled(store):
        assert store is not None
        pending = _record_pending(
            store=store,
            action="delete",
            category=category,
            model_id=model_id,
            model=None,
            error=None,
        )
        path = _journal_path(store)
        persisted, error = _delete_persisted_model_now(store, category, model_id)
        if persisted:
            _clear_pending_if_current(pending, path)
        else:
            _update_pending_failure(
                pending,
                path,
                error or "unknown D1 deletion failure",
            )
    else:
        _set_not_configured_state(category, model_id, "delete")

    with _LOCK:
        before = len(_REGISTRY[category])
        _REGISTRY[category] = [m for m in _REGISTRY[category] if m.model_id != model_id]
        return len(_REGISTRY[category]) != before


def set_model_lifecycle(
    model_id: str,
    *,
    lifecycle_status: str,
    expiration_date: str | None = None,
    canonical_slug: str | None = None,
    store: "D1MetadataStore | None" = None,
) -> int:
    """Update matching registry entries without deleting their audit history."""
    if lifecycle_status not in LIFECYCLE_STATUSES:
        raise ModelRegistryError(
            f"Unknown lifecycle status: {lifecycle_status!r}. Expected one of {LIFECYCLE_STATUSES}."
        )

    changed_by_key: dict[str, RankedModel] = {}
    with _LOCK:
        for category in CATEGORIES:
            for item in _REGISTRY[category]:
                if item.model_id not in {model_id, canonical_slug} and item.canonical_slug != model_id:
                    continue
                changed = replace(
                    item,
                    lifecycle_status=lifecycle_status,
                    expiration_date=expiration_date or item.expiration_date,
                    canonical_slug=canonical_slug or item.canonical_slug,
                )
                changed_by_key[_item_id(category, item.model_id)] = changed

    for changed in changed_by_key.values():
        if _store_enabled(store):
            assert store is not None
            pending = _record_pending(
                store=store,
                action="upsert",
                category=changed.category,
                model_id=changed.model_id,
                model=changed,
                error=None,
            )
            path = _journal_path(store)
            persisted, error = _persist_model_now(store, changed)
            if persisted:
                _clear_pending_if_current(pending, path)
            else:
                _update_pending_failure(
                    pending,
                    path,
                    error or "unknown D1 lifecycle persistence failure",
                )
        else:
            _set_not_configured_state(changed.category, changed.model_id, "upsert")

    with _LOCK:
        for category in CATEGORIES:
            _REGISTRY[category] = [
                changed_by_key.get(_item_id(category, item.model_id), item)
                for item in _REGISTRY[category]
            ]
    return len(changed_by_key)


# ---------- startup / reconciliation ----------


def _overlay_pending_locked() -> None:
    for operation in sorted(_PENDING.values(), key=lambda op: op.queued_at):
        _PERSISTENCE_STATE[operation.key] = {
            "state": "pending",
            "action": operation.action,
            "error": operation.last_error,
            "updated_at": operation.queued_at,
        }
        if operation.action == "delete":
            _REGISTRY[operation.category] = [
                item for item in _REGISTRY[operation.category] if item.model_id != operation.model_id
            ]
            continue
        if operation.action == "upsert" and operation.model:
            ranked = _ranked_from_mapping(operation.model)
            if ranked is None:
                continue
            existing = [
                item for item in _REGISTRY[ranked.category] if item.model_id != ranked.model_id
            ]
            existing.append(ranked)
            existing.sort(key=lambda item: item.score, reverse=True)
            _REGISTRY[ranked.category] = existing


def load_registry_from_store(store: "D1MetadataStore | None") -> int:
    """Rehydrate the registry from D1, then overlay locally journalled mutations.

    D1 outages at startup remain fail-open, but are logged. Pending mutations are
    still applied from the reconciliation journal so a failed delete cannot silently
    reappear merely because the process restarted.
    """
    path = _journal_path(store)
    with _LOCK:
        _PENDING.clear()
        _PERSISTENCE_STATE.clear()
    _load_pending_journal(path)

    result: dict[str, object] | None = None
    if _store_enabled(store):
        assert store is not None
        try:
            candidate = store.list_metadata(lane=LANE, limit=500)
            if isinstance(candidate, dict):
                result = candidate
        except Exception as exc:  # noqa: BLE001 - startup must remain available
            logger.error(
                "model_registry_load_failed error=%s",
                exc,
                extra={"event": "model_registry_load_failed", "error": str(exc)},
            )
        if result is not None and not result.get("ok"):
            logger.error(
                "model_registry_load_failed error=%s",
                _result_error(result),
                extra={
                    "event": "model_registry_load_failed",
                    "error": _result_error(result),
                },
            )
            result = None

    count = 0
    with _LOCK:
        for category in CATEGORIES:
            _REGISTRY[category] = []

        for row in (result or {}).get("items", []):
            if not isinstance(row, dict):
                continue
            metadata_raw = row.get("metadata") or {}
            metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
            metadata.setdefault("category", row.get("source_type"))
            metadata.setdefault("model_id", row.get("source_id"))
            ranked = _ranked_from_mapping(metadata)
            if ranked is None:
                continue
            _REGISTRY[ranked.category] = [
                item for item in _REGISTRY[ranked.category] if item.model_id != ranked.model_id
            ]
            _REGISTRY[ranked.category].append(ranked)
            _PERSISTENCE_STATE[_item_id(ranked.category, ranked.model_id)] = {
                "state": "durable",
                "action": "upsert",
                "error": None,
                "updated_at": time.time(),
            }
            count += 1
        for category in CATEGORIES:
            _REGISTRY[category].sort(key=lambda item: item.score, reverse=True)
        _overlay_pending_locked()
    return count


def reconcile_pending(store: "D1MetadataStore | None") -> dict[str, object]:
    """Retry queued D1 mutations once, safely and idempotently."""
    if not _store_enabled(store):
        return {
            "ok": False,
            "enabled": False,
            "pending_count": pending_reconciliation_count(),
            "reconciled_count": 0,
        }
    assert store is not None
    path = _journal_path(store)
    _set_active_journal_path(path)

    with _RECONCILE_LOCK:
        with _LOCK:
            snapshot = list(_PENDING.values())
        reconciled = 0
        failed = 0
        for operation in snapshot:
            _bump_metric("reconciliation_attempts")
            if operation.action == "delete":
                success, error = _delete_persisted_model_now(
                    store, operation.category, operation.model_id
                )
            else:
                ranked = _ranked_from_mapping(operation.model or {})
                if ranked is None:
                    success, error = False, "pending upsert payload is invalid"
                else:
                    success, error = _persist_model_now(store, ranked)

            if success:
                if _clear_pending_if_current(operation, path):
                    reconciled += 1
                    _bump_metric("reconciliation_successes")
                    logger.info(
                        "model_registry_reconciled action=%s category=%s model_id=%s",
                        operation.action,
                        operation.category,
                        operation.model_id,
                        extra={
                            "event": "model_registry_reconciled",
                            "action": operation.action,
                            "category": operation.category,
                            "model_id": operation.model_id,
                        },
                    )
                else:
                    with _LOCK:
                        current = _PENDING.get(operation.key)
                    if current is not None and current.operation_id == operation.operation_id:
                        failed += 1
                        _bump_metric("reconciliation_failures")
                        logger.error(
                            "model_registry_reconciliation_cleanup_pending action=%s category=%s model_id=%s",
                            operation.action,
                            operation.category,
                            operation.model_id,
                            extra={
                                "event": "model_registry_reconciliation_cleanup_pending",
                                "action": operation.action,
                                "category": operation.category,
                                "model_id": operation.model_id,
                            },
                        )
            else:
                failed += 1
                _bump_metric("reconciliation_failures")
                _update_pending_failure(
                    operation,
                    path,
                    error or "unknown D1 reconciliation failure",
                )
                logger.error(
                    "model_registry_reconciliation_failed action=%s category=%s model_id=%s error=%s",
                    operation.action,
                    operation.category,
                    operation.model_id,
                    error,
                    extra={
                        "event": "model_registry_reconciliation_failed",
                        "action": operation.action,
                        "category": operation.category,
                        "model_id": operation.model_id,
                        "error": error,
                    },
                )

        pending = pending_reconciliation_count()
        return {
            "ok": failed == 0,
            "enabled": True,
            "attempted_count": len(snapshot),
            "reconciled_count": reconciled,
            "failed_count": failed,
            "pending_count": pending,
        }


# ---------- read / diagnostics API ----------


def get_ranked_models(category: str) -> list[RankedModel]:
    _require_known_category(category)
    with _LOCK:
        return list(_REGISTRY[category])


def get_default_model(category: str) -> str | None:
    ranked = get_ranked_models(category)
    return next(
        (item.model_id for item in ranked if item.lifecycle_status in ROUTABLE_LIFECYCLE_STATUSES),
        None,
    )


def list_categories() -> dict[str, list[dict[str, object]]]:
    with _LOCK:
        return {
            category: [asdict(model) for model in models] for category, models in _REGISTRY.items()
        }


def get_persistence_state(category: str, model_id: str) -> dict[str, object]:
    _require_known_category(category)
    with _LOCK:
        state = _PERSISTENCE_STATE.get(_item_id(category, model_id))
        if state is None:
            return {
                "state": "unknown",
                "action": None,
                "error": None,
                "updated_at": None,
            }
        return dict(state)


def pending_reconciliation_count() -> int:
    with _LOCK:
        return len(_PENDING)


def persistence_diagnostics() -> dict[str, object]:
    with _LOCK:
        return {
            "pending_count": len(_PENDING),
            "pending": [asdict(item) for item in sorted(_PENDING.values(), key=lambda op: op.queued_at)],
            "metrics": dict(_METRICS),
            "journal_path": str(_ACTIVE_JOURNAL_PATH or DEFAULT_RECONCILIATION_PATH),
        }


def clear_registry(*, preserve_pending: bool = False) -> None:
    """Clear process state. `preserve_pending=True` simulates a process restart in tests."""
    with _LOCK:
        for category in CATEGORIES:
            _REGISTRY[category] = []
        if preserve_pending:
            return
        _PENDING.clear()
        _PERSISTENCE_STATE.clear()
        for name in _METRICS:
            _METRICS[name] = 0
        path = _ACTIVE_JOURNAL_PATH or DEFAULT_RECONCILIATION_PATH
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Unable to remove Model Registry reconciliation journal path=%s", path)


def seed_from_json(seed_json: str) -> int:
    """Populate the registry from an optional JSON seed.

    Malformed or unknown categories are skipped rather than raising, because the
    seed is optional startup configuration and should never crash boot.
    """
    if not seed_json or not seed_json.strip():
        return 0
    try:
        data = json.loads(seed_json)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0

    count = 0
    for category, entries in data.items():
        if category not in CATEGORIES or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("model_id"):
                continue
            try:
                score = float(entry.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            confidence = entry.get("confidence") or "unverified"
            if confidence not in CONFIDENCE_LEVELS:
                confidence = "unverified"
            register_model(
                category,
                str(entry["model_id"]),
                score=score,
                provider=entry.get("provider"),
                notes=entry.get("notes"),
                benchmark_score=_optional_float(entry, "benchmark_score"),
                confidence=confidence,
                latency_ms=_optional_float(entry, "latency_ms"),
                cost_per_1k_tokens=_optional_float(entry, "cost_per_1k_tokens"),
                canonical_slug=entry.get("canonical_slug"),
                expiration_date=entry.get("expiration_date"),
                lifecycle_status=(
                    str(entry.get("lifecycle_status") or "active")
                    if str(entry.get("lifecycle_status") or "active") in LIFECYCLE_STATUSES
                    else "active"
                ),
            )
            count += 1
    return count
