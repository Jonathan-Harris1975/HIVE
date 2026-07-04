from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.storage.d1 import D1MetadataStore

# Phase 2 - Repository Memory.
#
# Repository Memory persists structured, queryable knowledge about a
# repository that survives after its temporary working copy (Phase 1) is
# cleaned up. It is built entirely on the existing `hive_ecosystem_metadata`
# D1 table (lane/source_type/source_id/metadata_json) rather than a new
# schema, so no migration is required and the existing D1MetadataStore
# read/write/search paths are reused as-is.
#
# Each memory field is stored as its own row:
#   lane        = "repository_memory"
#   source_type = field name (e.g. "project_dna")
#   source_id   = repository_id
#   metadata    = the field's JSON-serialisable content
#
# List-shaped fields (known_issues, learned_patterns, previous_patches,
# optimisation_history, qa_history, repository_council_history) are stored as
# a JSON list under `metadata["items"]` and support append-only updates.

LANE = "repository_memory"

SCALAR_FIELDS = (
    "project_dna",
    "architecture_summary",
    "coding_standards",
    "build_profile",
    "deployment_profile",
    "environment_schema",
)

HISTORY_FIELDS = (
    "known_issues",
    "learned_patterns",
    "previous_patches",
    "optimisation_history",
    "qa_history",
    "repository_council_history",
)

ALL_FIELDS = SCALAR_FIELDS + HISTORY_FIELDS


class RepositoryMemoryError(ValueError):
    pass


def _require_known_field(field_name: str) -> None:
    if field_name not in ALL_FIELDS:
        raise RepositoryMemoryError(f"Unknown Repository Memory field: {field_name}")


def _item_id(repository_id: str, field_name: str) -> str:
    return f"repo-memory:{repository_id}:{field_name}"


@dataclass(frozen=True)
class RepositoryMemoryField:
    repository_id: str
    field_name: str
    content: Any
    updated_at: str | None


def set_memory_field(
    store: D1MetadataStore,
    *,
    repository_id: str,
    field_name: str,
    content: Any,
) -> dict[str, object]:
    """Set (overwrite) a scalar Repository Memory field."""
    _require_known_field(field_name)
    if field_name in HISTORY_FIELDS:
        raise RepositoryMemoryError(
            f"'{field_name}' is a history field; use append_history_entry instead"
        )
    return store.upsert_metadata(
        item_id=_item_id(repository_id, field_name),
        lane=LANE,
        source_type=field_name,
        source_id=repository_id,
        title=f"{field_name} for {repository_id}",
        url=None,
        metadata={"value": content},
    )


def append_history_entry(
    store: D1MetadataStore,
    *,
    repository_id: str,
    field_name: str,
    entry: dict[str, Any],
    max_entries: int = 200,
) -> dict[str, object]:
    """Append an entry to a history-shaped Repository Memory field.

    Reads the existing list (if any), appends the new entry, and truncates to
    the most recent `max_entries` so history fields cannot grow unbounded.
    """
    if field_name not in HISTORY_FIELDS:
        raise RepositoryMemoryError(f"'{field_name}' is not a history field")

    existing = get_memory_field(store, repository_id=repository_id, field_name=field_name)
    items: list[dict[str, Any]] = list(existing.content) if existing and existing.content else []
    items.append(entry)
    if len(items) > max_entries:
        items = items[-max_entries:]

    return store.upsert_metadata(
        item_id=_item_id(repository_id, field_name),
        lane=LANE,
        source_type=field_name,
        source_id=repository_id,
        title=f"{field_name} for {repository_id}",
        url=None,
        metadata={"value": items},
    )


def get_memory_field(
    store: D1MetadataStore,
    *,
    repository_id: str,
    field_name: str,
) -> RepositoryMemoryField | None:
    _require_known_field(field_name)
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return None
    for row in result.get("items", []):
        if row.get("source_type") == field_name and row.get("source_id") == repository_id:
            metadata = row.get("metadata") or {}
            return RepositoryMemoryField(
                repository_id=repository_id,
                field_name=field_name,
                content=metadata.get("value"),
                updated_at=row.get("updated_at"),
            )
    return None


def get_repository_memory(store: D1MetadataStore, *, repository_id: str) -> dict[str, Any]:
    """Return every stored Repository Memory field for a repository without
    requiring the repository's working copy to be loaded (Phase 1)."""
    memory: dict[str, Any] = {field_name: None for field_name in ALL_FIELDS}
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return memory
    for row in result.get("items", []):
        if row.get("source_id") != repository_id:
            continue
        field_name = row.get("source_type")
        if field_name in ALL_FIELDS:
            metadata = row.get("metadata") or {}
            memory[field_name] = metadata.get("value")
    return memory


def search_repository_memory(
    store: D1MetadataStore, *, query: str, repository_id: str | None = None, limit: int = 50
) -> dict[str, object]:
    """Queryable Repository Memory search without loading the full repository."""
    result = store.search_metadata(query=query, lane=LANE, limit=limit)
    if not result.get("ok") or repository_id is None:
        return result
    filtered = [item for item in result.get("items", []) if item.get("source_id") == repository_id]
    return {**result, "items": filtered, "count": len(filtered)}
