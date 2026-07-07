from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.storage.d1 import D1MetadataStore

# Phase 3 - Model Registry.
#
# A dynamic, in-process registry of ranked models per category. Categories
# are fixed (coding, reasoning, planning, vision, research, fast, cheap,
# creative, long_context); the *models* within each category are entirely
# configuration/runtime driven, not hard-coded. The highest-ranked model in
# a category is that category's default.
#
# This registry is additive: `app.services.model_router.ModelRouter` prefers
# the registry's "coding" default when the registry has been populated, and
# falls back to the existing `settings.code_model` value otherwise, so
# nothing breaks for deployments that never populate the registry.
#
# Persistence: the registry itself remains an in-memory cache for fast reads
# on the request path (`get_default_model` is called per-request by
# ModelRouter and must not block on network/DB I/O). To survive process
# restarts, registrations are additionally mirrored into the existing
# `hive_ecosystem_metadata` D1 table via D1MetadataStore, reusing the same
# lane/source_type/source_id pattern as Repository Memory
# (app/services/repository_memory.py). Callers that want persistence pass
# an optional `store` to register_model()/remove_model(); the in-memory-only
# API is unchanged for callers that don't (e.g. tests, the hot request
# path). `load_registry_from_store()` rehydrates `_REGISTRY` from D1 and is
# called once at application startup (see app/main.py lifespan), before any
# seed_json is applied, so persisted registrations take priority and
# seed_json only fills gaps.

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

_LOCK = threading.Lock()
_REGISTRY: dict[str, list["RankedModel"]] = {category: [] for category in CATEGORIES}


class ModelRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class RankedModel:
    model_id: str
    category: str
    score: float
    provider: str | None
    notes: str | None
    registered_at: float


def _require_known_category(category: str) -> None:
    if category not in CATEGORIES:
        raise ModelRegistryError(
            f"Unknown model category: {category!r}. Expected one of {CATEGORIES}."
        )


def _item_id(category: str, model_id: str) -> str:
    return f"model-registry:{category}:{model_id}"


def _persist_model(store: "D1MetadataStore | None", ranked: RankedModel) -> None:
    """Best-effort mirror of a registration into D1 so it survives a
    restart. Never raises: persistence is additive, and a D1 outage should
    not break in-memory registration on the request path."""
    if store is None:
        return
    try:
        if not store.enabled:
            return
        store.upsert_metadata(
            item_id=_item_id(ranked.category, ranked.model_id),
            lane=LANE,
            source_type=ranked.category,
            source_id=ranked.model_id,
            title=f"{ranked.category}:{ranked.model_id}",
            url=None,
            metadata={
                "model_id": ranked.model_id,
                "category": ranked.category,
                "score": ranked.score,
                "provider": ranked.provider,
                "notes": ranked.notes,
                "registered_at": ranked.registered_at,
            },
        )
    except Exception:  # noqa: BLE001 - persistence must never break registration
        pass


def _delete_persisted_model(store: "D1MetadataStore | None", category: str, model_id: str) -> None:
    if store is None:
        return
    try:
        if not store.enabled:
            return
        store.delete_metadata_ids([_item_id(category, model_id)])
    except Exception:  # noqa: BLE001 - persistence must never break removal
        pass


def register_model(
    category: str,
    model_id: str,
    *,
    score: float,
    provider: str | None = None,
    notes: str | None = None,
    store: "D1MetadataStore | None" = None,
) -> list[RankedModel]:
    """Register (or re-score) a model within a category and return the
    category's models re-ranked highest score first.

    If `store` is provided, the registration is also mirrored into D1 so it
    survives a process restart (see `load_registry_from_store`)."""
    _require_known_category(category)
    if not model_id:
        raise ModelRegistryError("model_id is required")

    ranked = RankedModel(
        model_id=model_id,
        category=category,
        score=float(score),
        provider=provider,
        notes=notes,
        registered_at=time.time(),
    )
    with _LOCK:
        existing = [m for m in _REGISTRY[category] if m.model_id != model_id]
        existing.append(ranked)
        existing.sort(key=lambda m: m.score, reverse=True)
        _REGISTRY[category] = existing
        result = list(existing)
    _persist_model(store, ranked)
    return result


def remove_model(category: str, model_id: str, *, store: "D1MetadataStore | None" = None) -> bool:
    _require_known_category(category)
    with _LOCK:
        before = len(_REGISTRY[category])
        _REGISTRY[category] = [m for m in _REGISTRY[category] if m.model_id != model_id]
        removed = len(_REGISTRY[category]) != before
    if removed:
        _delete_persisted_model(store, category, model_id)
    return removed


def load_registry_from_store(store: "D1MetadataStore | None") -> int:
    """Rehydrate `_REGISTRY` from D1 at startup. Returns the number of
    entries loaded. Safe to call with a disabled/unconfigured store (no-op)
    and never raises, so a D1 outage at boot degrades to an empty registry
    (falling back to static settings-driven defaults) rather than crashing
    startup."""
    if store is None:
        return 0
    try:
        if not store.enabled:
            return 0
        result = store.list_metadata(lane=LANE, limit=500)
    except Exception:  # noqa: BLE001 - startup must never crash on D1 issues
        return 0
    if not result.get("ok"):
        return 0

    count = 0
    with _LOCK:
        for category in CATEGORIES:
            _REGISTRY[category] = []
        for row in result.get("items", []):
            metadata = row.get("metadata") or {}
            category = metadata.get("category") or row.get("source_type")
            model_id = metadata.get("model_id") or row.get("source_id")
            if category not in CATEGORIES or not model_id:
                continue
            try:
                score = float(metadata.get("score", 0.0))
            except (TypeError, ValueError):
                score = 0.0
            ranked = RankedModel(
                model_id=str(model_id),
                category=category,
                score=score,
                provider=metadata.get("provider"),
                notes=metadata.get("notes"),
                registered_at=float(metadata.get("registered_at") or time.time()),
            )
            _REGISTRY[category] = [m for m in _REGISTRY[category] if m.model_id != model_id]
            _REGISTRY[category].append(ranked)
            count += 1
        for category in CATEGORIES:
            _REGISTRY[category].sort(key=lambda m: m.score, reverse=True)
    return count


def get_ranked_models(category: str) -> list[RankedModel]:
    _require_known_category(category)
    with _LOCK:
        return list(_REGISTRY[category])


def get_default_model(category: str) -> str | None:
    """Return the highest-ranked model_id for a category, or None if the
    category has no registered models (caller should fall back to static
    configuration in that case)."""
    ranked = get_ranked_models(category)
    return ranked[0].model_id if ranked else None


def list_categories() -> dict[str, list[dict[str, object]]]:
    with _LOCK:
        return {
            category: [asdict(model) for model in models] for category, models in _REGISTRY.items()
        }


def clear_registry() -> None:
    with _LOCK:
        for category in CATEGORIES:
            _REGISTRY[category] = []


def seed_from_json(seed_json: str) -> int:
    """Populate the registry from a JSON seed of the form:

        {"coding": [{"model_id": "...", "score": 0.9, "provider": "openrouter"}], ...}

    Returns the number of model entries registered. Malformed or unknown
    categories are skipped rather than raising, since this seed is optional
    startup configuration and should never crash boot.
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
            register_model(
                category,
                str(entry["model_id"]),
                score=score,
                provider=entry.get("provider"),
                notes=entry.get("notes"),
            )
            count += 1
    return count
