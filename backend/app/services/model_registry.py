from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from app.core.config import Settings
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
# hive_ecosystem_metadata table. Each configured mutation is first represented
# in a private R2 operation log. If D1 is temporarily unavailable, that external
# operation remains pending and is overlaid after a completely fresh instance
# starts. Reconciliation uses cross-instance claims and idempotent D1 writes.

LANE = "model_registry"

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
_PENDING_STORE_DIAGNOSTICS: dict[str, object] = {
    "backend": "r2",
    "enabled": False,
    "lane": "meta_system",
    "bucket_configured": False,
    "prefix": "state/hive/model-registry-pending",
}
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
    """Raised when neither D1 nor the external pending store can protect a mutation."""


class DurablePendingStore(Protocol):
    enabled: bool

    def safe_config(self) -> dict[str, object]: ...

    def persist(self, operation: dict[str, object]) -> None: ...

    def load(self) -> list[dict[str, object]]: ...

    def claim(self, operation: dict[str, object]) -> bool: ...

    def complete(self, operation: dict[str, object]) -> None: ...

    def record_failure(self, operation: dict[str, object]) -> None: ...

    def release_claim(self, operation: dict[str, object]) -> None: ...

    def discard(self, operation: dict[str, object]) -> None: ...


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


# ---------- externally durable reconciliation operations ----------


def _set_pending_store_diagnostics(value: dict[str, object]) -> None:
    with _LOCK:
        _PENDING_STORE_DIAGNOSTICS.clear()
        _PENDING_STORE_DIAGNOSTICS.update(value)


def _resolve_pending_store(
    store: "D1MetadataStore | None",
    pending_store: DurablePendingStore | None = None,
) -> DurablePendingStore | None:
    if pending_store is not None:
        _set_pending_store_diagnostics(pending_store.safe_config())
        return pending_store
    injected = getattr(store, "model_registry_pending_store", None)
    if injected is not None:
        _set_pending_store_diagnostics(injected.safe_config())
        return injected
    settings: "Settings | None" = getattr(store, "settings", None)
    if settings is None:
        return None
    from app.storage.model_registry_pending import R2ModelRegistryPendingStore

    resolved = R2ModelRegistryPendingStore(settings)
    _set_pending_store_diagnostics(resolved.safe_config())
    return resolved


def _pending_from_mapping(item: dict[str, object]) -> PendingOperation | None:
    action = str(item.get("action") or "")
    category = str(item.get("category") or "")
    model_id = str(item.get("model_id") or "")
    operation_id = str(item.get("operation_id") or "")
    if (
        action not in {"upsert", "delete"}
        or category not in CATEGORIES
        or not model_id
        or not operation_id
    ):
        return None
    raw_queued_at = item.get("queued_at")
    raw_attempts = item.get("attempts")
    raw_model = item.get("model")
    try:
        queued_at = float(raw_queued_at) if isinstance(raw_queued_at, (int, float, str)) else 0.0
        attempts = int(raw_attempts) if isinstance(raw_attempts, (int, float, str)) else 0
    except (TypeError, ValueError):
        return None
    if queued_at <= 0:
        return None
    return PendingOperation(
        operation_id=operation_id,
        action=action,
        category=category,
        model_id=model_id,
        queued_at=queued_at,
        model=cast(dict[str, object], raw_model) if isinstance(raw_model, dict) else None,
        attempts=max(0, attempts),
        last_error=str(item["last_error"]) if item.get("last_error") is not None else None,
    )


def _load_pending_store(pending_store: DurablePendingStore | None) -> int:
    if pending_store is None or not pending_store.enabled:
        return 0
    try:
        raw_operations = pending_store.load()
    except Exception as exc:  # noqa: BLE001 - fail closed instead of losing accepted work
        logger.error(
            "model_registry_pending_store_load_failed error_type=%s",
            type(exc).__name__,
            extra={"event": "model_registry_pending_store_load_failed"},
        )
        raise ModelRegistryPersistenceError(
            "Model Registry durable pending operations could not be loaded"
        ) from exc

    latest: dict[str, PendingOperation] = {}
    for item in raw_operations:
        operation = _pending_from_mapping(item)
        if operation is None:
            raise ModelRegistryPersistenceError(
                "Model Registry durable pending store contains an invalid operation"
            )
        current = latest.get(operation.key)
        if current is None or (operation.queued_at, operation.operation_id) > (
            current.queued_at,
            current.operation_id,
        ):
            latest[operation.key] = operation

    with _LOCK:
        _PENDING.clear()
        for operation in latest.values():
            _PENDING[operation.key] = operation
            _PERSISTENCE_STATE[operation.key] = {
                "state": "pending",
                "action": operation.action,
                "error": operation.last_error,
                "updated_at": operation.queued_at,
            }
    if latest:
        logger.warning(
            "model_registry_pending_restored count=%s backend=r2",
            len(latest),
            extra={"event": "model_registry_pending_restored", "count": len(latest)},
        )
    return len(latest)


def _new_pending_operation(
    *,
    action: str,
    category: str,
    model_id: str,
    model: RankedModel | None,
) -> PendingOperation:
    return PendingOperation(
        operation_id=uuid.uuid4().hex,
        action=action,
        category=category,
        model_id=model_id,
        queued_at=time.time(),
        model=_metadata_for_ranked(model) if model is not None else None,
    )


def _record_pending(
    pending_store: DurablePendingStore,
    operation: PendingOperation,
) -> None:
    try:
        pending_store.persist(asdict(operation))
    except Exception as exc:  # noqa: BLE001 - caller may still commit directly to D1
        logger.error(
            "model_registry_pending_store_write_failed action=%s category=%s model_id=%s error_type=%s",
            operation.action,
            operation.category,
            operation.model_id,
            type(exc).__name__,
            extra={
                "event": "model_registry_pending_store_write_failed",
                "action": operation.action,
                "category": operation.category,
                "model_id": operation.model_id,
            },
        )
        raise ModelRegistryPersistenceError(
            "Model Registry durable R2 fallback could not accept the operation"
        ) from exc
    with _LOCK:
        _PENDING[operation.key] = operation
        _PERSISTENCE_STATE[operation.key] = {
            "state": "pending",
            "action": operation.action,
            "error": operation.last_error,
            "updated_at": operation.queued_at,
        }


def _set_durable_state(operation: PendingOperation) -> None:
    with _LOCK:
        _PENDING.pop(operation.key, None)
        _PERSISTENCE_STATE[operation.key] = {
            "state": "durable",
            "action": operation.action,
            "error": None,
            "updated_at": time.time(),
        }


def _clear_pending_if_current(
    operation: PendingOperation,
    pending_store: DurablePendingStore,
) -> bool:
    with _LOCK:
        current = _PENDING.get(operation.key)
        if current is None or current.operation_id != operation.operation_id:
            return False
    try:
        pending_store.complete(asdict(operation))
    except Exception as exc:  # noqa: BLE001 - D1 is durable; retain safe idempotent work
        error = f"D1 durable; R2 pending-operation cleanup failed ({type(exc).__name__})"
        restored = replace(operation, last_error=error)
        with _LOCK:
            current = _PENDING.get(operation.key)
            if current is not None and current.operation_id == operation.operation_id:
                _PENDING[operation.key] = restored
                _PERSISTENCE_STATE[operation.key] = {
                    "state": "pending",
                    "action": operation.action,
                    "error": error,
                    "updated_at": time.time(),
                }
        return False

    with _LOCK:
        current = _PENDING.get(operation.key)
        if current is None or current.operation_id != operation.operation_id:
            return False
        _set_durable_state(operation)
    return True


def _update_pending_failure(
    operation: PendingOperation,
    pending_store: DurablePendingStore,
    error: str,
) -> PendingOperation:
    with _LOCK:
        current = _PENDING.get(operation.key)
        if current is None or current.operation_id != operation.operation_id:
            return operation
        updated = replace(current, attempts=current.attempts + 1, last_error=error)
        _PENDING[operation.key] = updated
        _PERSISTENCE_STATE[operation.key] = {
            "state": "pending",
            "action": updated.action,
            "error": error,
            "updated_at": time.time(),
        }
    try:
        pending_store.record_failure(asdict(updated))
    except Exception as exc:  # noqa: BLE001 - the original operation remains durable
        logger.error(
            "model_registry_pending_diagnostic_update_failed operation_id=%s error_type=%s",
            operation.operation_id,
            type(exc).__name__,
            extra={"event": "model_registry_pending_diagnostic_update_failed"},
        )
    return updated


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


def _persist_or_queue_mutation(
    *,
    store: "D1MetadataStore | None",
    pending_store: DurablePendingStore | None,
    action: str,
    category: str,
    model_id: str,
    model: RankedModel | None,
    failure_message: str,
) -> None:
    if not _store_enabled(store):
        _set_not_configured_state(category, model_id, action)
        return

    assert store is not None
    resolved_pending_store = _resolve_pending_store(store, pending_store)
    operation = _new_pending_operation(
        action=action,
        category=category,
        model_id=model_id,
        model=model,
    )
    queued = bool(resolved_pending_store is not None and resolved_pending_store.enabled)
    if queued:
        assert resolved_pending_store is not None
        _record_pending(resolved_pending_store, operation)

    if action == "delete":
        persisted, error = _delete_persisted_model_now(store, category, model_id)
    else:
        if model is None:
            raise ModelRegistryPersistenceError("Model Registry upsert payload is missing")
        persisted, error = _persist_model_now(store, model)

    if persisted:
        if queued:
            assert resolved_pending_store is not None
            _clear_pending_if_current(operation, resolved_pending_store)
        else:
            _set_durable_state(operation)
        return

    if not queued or resolved_pending_store is None:
        raise ModelRegistryPersistenceError(
            f"{failure_message}; durable R2 fallback is unavailable"
        )
    _update_pending_failure(operation, resolved_pending_store, error or failure_message)


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
    pending_store: DurablePendingStore | None = None,
) -> list[RankedModel]:
    """Register or re-score a model and mirror the mutation durably when configured.

    D1 failure remains available only when the mutation was first written to the
    external R2 operation log. If both durable paths fail, the mutation is rejected.
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

    _persist_or_queue_mutation(
        store=store,
        pending_store=pending_store,
        action="upsert",
        category=category,
        model_id=model_id,
        model=ranked,
        failure_message="D1 Model Registry persistence failed",
    )

    with _LOCK:
        existing = [m for m in _REGISTRY[category] if m.model_id != model_id]
        existing.append(ranked)
        existing.sort(key=lambda m: m.score, reverse=True)
        _REGISTRY[category] = existing
        return list(existing)


def remove_model(
    category: str,
    model_id: str,
    *,
    store: "D1MetadataStore | None" = None,
    pending_store: DurablePendingStore | None = None,
) -> bool:
    _require_known_category(category)
    with _LOCK:
        exists = any(m.model_id == model_id for m in _REGISTRY[category])
    if not exists:
        return False

    _persist_or_queue_mutation(
        store=store,
        pending_store=pending_store,
        action="delete",
        category=category,
        model_id=model_id,
        model=None,
        failure_message="D1 Model Registry deletion failed",
    )

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
    pending_store: DurablePendingStore | None = None,
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
        _persist_or_queue_mutation(
            store=store,
            pending_store=pending_store,
            action="upsert",
            category=changed.category,
            model_id=changed.model_id,
            model=changed,
            failure_message="D1 Model Registry lifecycle persistence failed",
        )

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


def load_registry_from_store(
    store: "D1MetadataStore | None",
    pending_store: DurablePendingStore | None = None,
) -> int:
    """Rehydrate D1 state, then overlay the externally durable R2 operation log."""
    resolved_pending_store = _resolve_pending_store(store, pending_store)
    with _LOCK:
        _PENDING.clear()
        _PERSISTENCE_STATE.clear()
    _load_pending_store(resolved_pending_store)

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

        raw_items = (result or {}).get("items", [])
        rows = raw_items if isinstance(raw_items, list) else []
        for row in rows:
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


def reconcile_pending(
    store: "D1MetadataStore | None",
    pending_store: DurablePendingStore | None = None,
) -> dict[str, object]:
    """Claim and retry queued D1 mutations once, safely and idempotently."""
    if not _store_enabled(store):
        return {
            "ok": False,
            "enabled": False,
            "pending_count": pending_reconciliation_count(),
            "reconciled_count": 0,
        }
    assert store is not None
    resolved_pending_store = _resolve_pending_store(store, pending_store)
    if resolved_pending_store is None or not resolved_pending_store.enabled:
        pending_count = pending_reconciliation_count()
        return {
            "ok": pending_count == 0,
            "enabled": True,
            "durable_store_enabled": False,
            "attempted_count": 0,
            "reconciled_count": 0,
            "failed_count": pending_count,
            "pending_count": pending_count,
        }

    with _RECONCILE_LOCK:
        with _LOCK:
            snapshot = list(_PENDING.values())
        reconciled = 0
        failed = 0
        claimed_count = 0
        claim_skipped_count = 0
        for operation in snapshot:
            _bump_metric("reconciliation_attempts")
            try:
                claimed = resolved_pending_store.claim(asdict(operation))
            except Exception as exc:  # noqa: BLE001 - retain pending work for a later retry
                failed += 1
                _bump_metric("reconciliation_failures")
                logger.error(
                    "model_registry_reconciliation_claim_failed operation_id=%s error_type=%s",
                    operation.operation_id,
                    type(exc).__name__,
                    extra={"event": "model_registry_reconciliation_claim_failed"},
                )
                continue
            if not claimed:
                claim_skipped_count += 1
                continue
            claimed_count += 1
            with _LOCK:
                current = _PENDING.get(operation.key)
            if current is None or current.operation_id != operation.operation_id:
                try:
                    resolved_pending_store.release_claim(asdict(operation))
                except Exception:  # noqa: BLE001 - the bounded claim expires automatically
                    pass
                claim_skipped_count += 1
                continue

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
                if _clear_pending_if_current(operation, resolved_pending_store):
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
                        try:
                            resolved_pending_store.release_claim(asdict(operation))
                        except Exception:  # noqa: BLE001 - the bounded claim expires automatically
                            pass
            else:
                failed += 1
                _bump_metric("reconciliation_failures")
                updated = _update_pending_failure(
                    operation,
                    resolved_pending_store,
                    error or "unknown D1 reconciliation failure",
                )
                try:
                    resolved_pending_store.release_claim(asdict(updated))
                except Exception as exc:  # noqa: BLE001 - lease expiry provides bounded recovery
                    logger.error(
                        "model_registry_reconciliation_claim_release_failed operation_id=%s error_type=%s",
                        operation.operation_id,
                        type(exc).__name__,
                        extra={"event": "model_registry_reconciliation_claim_release_failed"},
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
            "durable_store_enabled": True,
            "attempted_count": len(snapshot),
            "claimed_count": claimed_count,
            "claim_skipped_count": claim_skipped_count,
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
            "pending": [
                {
                    "operation_id": item.operation_id,
                    "action": item.action,
                    "category": item.category,
                    "model_id": item.model_id,
                    "queued_at": item.queued_at,
                    "attempts": item.attempts,
                    "last_error": item.last_error,
                }
                for item in sorted(_PENDING.values(), key=lambda op: op.queued_at)
            ],
            "metrics": dict(_METRICS),
            "durable_pending_store": dict(_PENDING_STORE_DIAGNOSTICS),
        }


def clear_registry(*, preserve_pending: bool = False) -> None:
    """Clear process memory without deleting externally durable pending operations."""
    with _LOCK:
        for category in CATEGORIES:
            _REGISTRY[category] = []
        if preserve_pending:
            return
        _PENDING.clear()
        _PERSISTENCE_STATE.clear()
        for name in _METRICS:
            _METRICS[name] = 0


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
