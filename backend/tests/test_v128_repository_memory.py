from __future__ import annotations

import pytest

from app.services import repository_memory as rmem


class FakeD1Store:
    """In-memory double matching the subset of D1MetadataStore used by
    repository_memory.py, so these tests never touch the network."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        self._rows[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "url": url,
            "metadata": metadata,
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
        return {"ok": True, "enabled": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [row for row in self._rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "enabled": True, "count": len(items), "items": list(items)}

    def search_metadata(self, *, query, lane=None, limit=50):
        needle = query.lower()
        items = [
            row
            for row in self._rows.values()
            if (lane is None or row["lane"] == lane)
            and needle in str(row).lower()
        ]
        return {"ok": True, "enabled": True, "count": len(items), "items": list(items)}


@pytest.fixture
def store():
    return FakeD1Store()


def test_set_and_get_scalar_field(store):
    rmem.set_memory_field(
        store, repository_id="repo-1", field_name="project_dna", content={"summary": "FastAPI service"}
    )
    result = rmem.get_memory_field(store, repository_id="repo-1", field_name="project_dna")
    assert result is not None
    assert result.content == {"summary": "FastAPI service"}


def test_set_memory_field_rejects_history_field(store):
    with pytest.raises(rmem.RepositoryMemoryError):
        rmem.set_memory_field(store, repository_id="repo-1", field_name="known_issues", content=[])


def test_set_memory_field_rejects_unknown_field(store):
    with pytest.raises(rmem.RepositoryMemoryError):
        rmem.set_memory_field(store, repository_id="repo-1", field_name="not_a_field", content={})


def test_append_history_entry_accumulates_and_truncates(store):
    for i in range(5):
        rmem.append_history_entry(
            store,
            repository_id="repo-1",
            field_name="qa_history",
            entry={"run": i},
            max_entries=3,
        )
    result = rmem.get_memory_field(store, repository_id="repo-1", field_name="qa_history")
    assert result is not None
    assert [item["run"] for item in result.content] == [2, 3, 4]


def test_append_history_entry_rejects_scalar_field(store):
    with pytest.raises(rmem.RepositoryMemoryError):
        rmem.append_history_entry(
            store, repository_id="repo-1", field_name="project_dna", entry={"x": 1}
        )


def test_get_repository_memory_returns_all_fields_with_none_defaults(store):
    rmem.set_memory_field(store, repository_id="repo-1", field_name="build_profile", content="docker")
    memory = rmem.get_repository_memory(store, repository_id="repo-1")
    assert set(memory.keys()) == set(rmem.ALL_FIELDS)
    assert memory["build_profile"] == "docker"
    assert memory["known_issues"] is None


def test_memory_is_scoped_per_repository(store):
    rmem.set_memory_field(store, repository_id="repo-1", field_name="project_dna", content="repo-1-dna")
    rmem.set_memory_field(store, repository_id="repo-2", field_name="project_dna", content="repo-2-dna")
    memory_1 = rmem.get_repository_memory(store, repository_id="repo-1")
    memory_2 = rmem.get_repository_memory(store, repository_id="repo-2")
    assert memory_1["project_dna"] == "repo-1-dna"
    assert memory_2["project_dna"] == "repo-2-dna"


def test_search_repository_memory_filters_by_repository(store):
    rmem.set_memory_field(
        store, repository_id="repo-1", field_name="architecture_summary", content="FastAPI + Koyeb"
    )
    rmem.set_memory_field(
        store, repository_id="repo-2", field_name="architecture_summary", content="FastAPI + Koyeb"
    )
    result = rmem.search_repository_memory(store, query="fastapi", repository_id="repo-1")
    assert result["ok"] is True
    assert all(item["source_id"] == "repo-1" for item in result["items"])
