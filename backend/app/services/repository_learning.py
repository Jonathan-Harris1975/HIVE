from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.services.repository_memory import (
    append_history_entry,
    get_repository_memory,
    set_memory_field,
)
from app.storage.d1 import D1MetadataStore

# Phase 12 - Repository Learning.
#
# Builds entirely on Phase 2's Repository Memory fields (previous_patches,
# learned_patterns) — no new storage. This module adds the *learning* layer
# on top: recording patch outcomes and coding patterns, and periodically
# rolling that history up into an automatically refreshed `project_dna`
# summary field, so a repository's Project DNA reflects what HIVE has
# actually learned about it rather than staying static after Phase 1/2
# initial population.


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def record_patch_outcome(
    settings: Settings,
    *,
    repository_id: str,
    summary: str,
    success: bool,
    files_changed: list[str] | None = None,
) -> dict[str, Any]:
    store = D1MetadataStore(settings)
    entry = {
        "summary": summary,
        "success": success,
        "files_changed": files_changed or [],
        "recorded_at": _now_iso(),
    }
    append_history_entry(store, repository_id=repository_id, field_name="previous_patches", entry=entry)
    return entry


def record_coding_pattern(
    settings: Settings, *, repository_id: str, pattern: str, context: str = ""
) -> dict[str, Any]:
    store = D1MetadataStore(settings)
    entry = {"pattern": pattern, "context": context, "recorded_at": _now_iso()}
    append_history_entry(store, repository_id=repository_id, field_name="learned_patterns", entry=entry)
    return entry


def record_preferred_model(
    settings: Settings, *, repository_id: str, category: str, model_id: str, reason: str = ""
) -> dict[str, Any]:
    """Preferred models are recorded as a learned pattern scoped to model
    selection, rather than a new Repository Memory field, since Phase 3's
    Model Registry already owns global model ranking — this only captures
    *this repository's* observed preference for later reference."""
    return record_coding_pattern(
        settings,
        repository_id=repository_id,
        pattern=f"preferred_model:{category}:{model_id}",
        context=reason,
    )


def _summarise_patches(patches: list[dict[str, Any]]) -> str:
    if not patches:
        return "No recorded patch history yet."
    successes = sum(1 for p in patches if p.get("success"))
    return f"{len(patches)} recorded patch(es), {successes} successful ({successes / len(patches):.0%})."


def _summarise_patterns(patterns: list[dict[str, Any]]) -> str:
    if not patterns:
        return "No recorded coding patterns yet."
    recent = [p.get("pattern") for p in patterns[-5:] if p.get("pattern")]
    return f"{len(patterns)} recorded pattern(s). Most recent: {', '.join(recent)}."


def update_project_dna(settings: Settings, *, repository_id: str) -> dict[str, Any]:
    """Roll up learned_patterns/previous_patches/qa_history/
    repository_council_history into a refreshed project_dna summary."""
    store = D1MetadataStore(settings)
    memory = get_repository_memory(store, repository_id=repository_id)

    patches = memory.get("previous_patches") or []
    patterns = memory.get("learned_patterns") or []
    qa_history = memory.get("qa_history") or []
    council_history = memory.get("repository_council_history") or []

    dna = {
        "patch_summary": _summarise_patches(patches),
        "pattern_summary": _summarise_patterns(patterns),
        "latest_qa_score": (qa_history[-1].get("score") if qa_history else None),
        "latest_council_score": (council_history[-1].get("overall_score") if council_history else None),
        "updated_at": _now_iso(),
    }
    set_memory_field(store, repository_id=repository_id, field_name="project_dna", content=dna)
    return dna
