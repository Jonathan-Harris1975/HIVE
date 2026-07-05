from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import repository_learning as rl


class FakeD1Store:
    _shared_rows: dict[str, dict] = {}

    def __init__(self, _settings=None) -> None:
        pass

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        FakeD1Store._shared_rows[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "url": url,
            "metadata": metadata,
        }
        return {"ok": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [row for row in FakeD1Store._shared_rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "count": len(items), "items": items}


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch):
    FakeD1Store._shared_rows = {}
    monkeypatch.setattr(rl, "D1MetadataStore", FakeD1Store)
    yield


@pytest.fixture
def settings():
    return Settings()


def test_record_patch_outcome_appends_entry(settings):
    entry = rl.record_patch_outcome(
        settings, repository_id="repo-1", summary="Fixed bug", success=True, files_changed=["a.py"]
    )
    assert entry["success"] is True
    assert entry["files_changed"] == ["a.py"]


def test_record_coding_pattern_appends_entry(settings):
    entry = rl.record_coding_pattern(settings, repository_id="repo-1", pattern="uses FastAPI dependency injection")
    assert entry["pattern"] == "uses FastAPI dependency injection"


def test_record_preferred_model_records_as_coding_pattern(settings):
    entry = rl.record_preferred_model(
        settings, repository_id="repo-1", category="coding", model_id="acme/coder", reason="fast + cheap"
    )
    assert entry["pattern"] == "preferred_model:coding:acme/coder"
    assert entry["context"] == "fast + cheap"


def test_update_project_dna_summarises_history(settings):
    rl.record_patch_outcome(settings, repository_id="repo-1", summary="p1", success=True)
    rl.record_patch_outcome(settings, repository_id="repo-1", summary="p2", success=False)
    rl.record_coding_pattern(settings, repository_id="repo-1", pattern="pattern-a")

    dna = rl.update_project_dna(settings, repository_id="repo-1")

    assert "2 recorded patch(es)" in dna["patch_summary"]
    assert "1 recorded pattern(s)" in dna["pattern_summary"]
    assert dna["latest_qa_score"] is None
    assert dna["latest_council_score"] is None


def test_update_project_dna_with_no_history_reports_gracefully(settings):
    dna = rl.update_project_dna(settings, repository_id="empty-repo")
    assert dna["patch_summary"] == "No recorded patch history yet."
    assert dna["pattern_summary"] == "No recorded coding patterns yet."
