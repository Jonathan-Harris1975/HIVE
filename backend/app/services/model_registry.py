from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass

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


def register_model(
    category: str,
    model_id: str,
    *,
    score: float,
    provider: str | None = None,
    notes: str | None = None,
) -> list[RankedModel]:
    """Register (or re-score) a model within a category and return the
    category's models re-ranked highest score first."""
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
        return list(existing)


def remove_model(category: str, model_id: str) -> bool:
    _require_known_category(category)
    with _LOCK:
        before = len(_REGISTRY[category])
        _REGISTRY[category] = [m for m in _REGISTRY[category] if m.model_id != model_id]
        return len(_REGISTRY[category]) != before


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
