from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

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
    "project_manifest",
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
    "repository_intelligence_history",
)

ALL_FIELDS = SCALAR_FIELDS + HISTORY_FIELDS


class RepositoryMemoryError(ValueError):
    pass


class RepositoryMemoryUnavailableError(RuntimeError):
    """Raised when the D1 persistence layer cannot serve Repository Memory."""


def _require_store_enabled(store: D1MetadataStore) -> None:
    if getattr(store, "enabled", True) is False:
        raise RepositoryMemoryUnavailableError(
            "Repository Memory persistence is unavailable because Cloudflare D1 is disabled or incomplete."
        )


def _require_store_result(result: dict[str, object], action: str) -> dict[str, object]:
    if result.get("ok"):
        return result
    detail = result.get("message") or result.get("error") or "Cloudflare D1 request failed."
    raise RepositoryMemoryUnavailableError(f"Repository Memory {action} failed: {detail}")


def _metadata_rows(result: dict[str, object]) -> list[dict[str, Any]]:
    """Return D1 metadata rows with a concrete type after runtime validation."""
    raw_items = result.get("items")
    if not isinstance(raw_items, list):
        return []
    return [cast(dict[str, Any], row) for row in raw_items if isinstance(row, dict)]


def _require_known_field(field_name: str) -> None:
    if field_name not in ALL_FIELDS:
        raise RepositoryMemoryError(f"Unknown Repository Memory field: {field_name}")


def repository_memory_item_id(repository_id: str, field_name: str) -> str:
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
    _require_store_enabled(store)
    if field_name in HISTORY_FIELDS:
        raise RepositoryMemoryError(
            f"'{field_name}' is a history field; use append_history_entry instead"
        )
    result = store.upsert_metadata(
        item_id=repository_memory_item_id(repository_id, field_name),
        lane=LANE,
        source_type=field_name,
        source_id=repository_id,
        title=f"{field_name} for {repository_id}",
        url=None,
        metadata={"value": content},
    )
    return _require_store_result(result, "write")


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
    _require_store_enabled(store)

    existing = get_memory_field(store, repository_id=repository_id, field_name=field_name)
    items: list[dict[str, Any]] = list(existing.content) if existing and existing.content else []
    items.append(entry)
    if len(items) > max_entries:
        items = items[-max_entries:]

    result = store.upsert_metadata(
        item_id=repository_memory_item_id(repository_id, field_name),
        lane=LANE,
        source_type=field_name,
        source_id=repository_id,
        title=f"{field_name} for {repository_id}",
        url=None,
        metadata={"value": items},
    )
    return _require_store_result(result, "history append")


def get_memory_field(
    store: D1MetadataStore,
    *,
    repository_id: str,
    field_name: str,
) -> RepositoryMemoryField | None:
    _require_known_field(field_name)
    _require_store_enabled(store)
    result = _require_store_result(store.list_metadata(lane=LANE, limit=500), "read")
    for row in _metadata_rows(result):
        if row.get("source_type") == field_name and row.get("source_id") == repository_id:
            raw_metadata = row.get("metadata")
            metadata = cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            return RepositoryMemoryField(
                repository_id=repository_id,
                field_name=field_name,
                content=metadata.get("value"),
                updated_at=str(row.get("updated_at")) if row.get("updated_at") is not None else None,
            )
    return None


def get_repository_memory(store: D1MetadataStore, *, repository_id: str) -> dict[str, Any]:
    """Return every stored Repository Memory field for a repository without
    requiring the repository's working copy to be loaded (Phase 1)."""
    _require_store_enabled(store)
    memory: dict[str, Any] = {field_name: None for field_name in ALL_FIELDS}
    result = _require_store_result(store.list_metadata(lane=LANE, limit=500), "read")
    for row in _metadata_rows(result):
        if row.get("source_id") != repository_id:
            continue
        field_name = row.get("source_type")
        if field_name in ALL_FIELDS:
            raw_metadata = row.get("metadata")
            metadata = cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
            memory[field_name] = metadata.get("value")
    return memory


def search_repository_memory(
    store: D1MetadataStore, *, query: str, repository_id: str | None = None, limit: int = 50
) -> dict[str, object]:
    """Queryable Repository Memory search without loading the full repository."""
    _require_store_enabled(store)
    result = _require_store_result(
        store.search_metadata(query=query, lane=LANE, limit=limit),
        "search",
    )
    if repository_id is None:
        return result
    filtered = [item for item in _metadata_rows(result) if item.get("source_id") == repository_id]
    return {**result, "items": filtered, "count": len(filtered)}


def migrate_repository_memory_id(
    store: D1MetadataStore,
    *,
    old_repository_id: str,
    new_repository_id: str,
) -> dict[str, object]:
    """Move Repository Memory rows from a legacy id to a stable repository id."""
    if old_repository_id == new_repository_id:
        return {"ok": True, "migrated_count": 0}
    _require_store_enabled(store)
    result = _require_store_result(store.list_metadata(lane=LANE, limit=500), "migration read")
    migrated_ids: list[str] = []
    old_ids: list[str] = []
    for row in _metadata_rows(result):
        if row.get("source_id") != old_repository_id:
            continue
        field_name = str(row.get("source_type") or "")
        if field_name not in ALL_FIELDS:
            continue
        write = store.upsert_metadata(
            item_id=repository_memory_item_id(new_repository_id, field_name),
            lane=LANE,
            source_type=field_name,
            source_id=new_repository_id,
            title=f"{field_name} for {new_repository_id}",
            url=row.get("url"),
            metadata=(
                cast(dict[str, Any], row.get("metadata"))
                if isinstance(row.get("metadata"), dict)
                else {}
            ),
        )
        _require_store_result(write, "migration write")
        migrated_ids.append(field_name)
        old_ids.append(repository_memory_item_id(old_repository_id, field_name))

    if old_ids and hasattr(store, "delete_metadata_ids"):
        delete_result = store.delete_metadata_ids(old_ids)
        _require_store_result(delete_result, "migration cleanup")
    return {"ok": True, "migrated_count": len(migrated_ids), "fields": migrated_ids}
