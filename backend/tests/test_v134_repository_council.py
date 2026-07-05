from __future__ import annotations

import io
import zipfile

import pytest

from app.core.config import Settings
from app.services import repository_council, repository_manager as rm


class FakeD1Store:
    def __init__(self, _settings=None) -> None:
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
        }
        return {"ok": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [row for row in self._rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "count": len(items), "items": items}


def _build_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    rm._REGISTRY.clear()
    shared_store = FakeD1Store()
    monkeypatch.setattr(repository_council, "D1MetadataStore", lambda settings: shared_store)
    yield
    for repository_id in list(rm._REGISTRY.keys()):
        rm.cleanup_repository(repository_id)


@pytest.fixture
def settings(tmp_path):
    return Settings(REPOSITORY_TEMP_DIR=str(tmp_path))


def test_run_repository_council_returns_nine_dimensions(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "def hello() -> str:\n    return 'hi'\n", "README.md": "# demo\n"}),
        settings=settings,
        source_filename="demo.zip",
    )

    report = repository_council.run_repository_council(settings, manifest.repository_id)

    assert {d.dimension for d in report.dimensions} == set(repository_council.DIMENSIONS)
    assert 0.0 <= report.overall_score <= 1.0


def test_missing_readme_lowers_documentation_score(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "x = 1\n"}), settings=settings, source_filename="demo.zip"
    )

    report = repository_council.run_repository_council(settings, manifest.repository_id)

    documentation = next(d for d in report.dimensions if d.dimension == "documentation")
    assert documentation.score == 0.0


def test_custom_weights_change_overall_score(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "x = 1\n"}), settings=settings, source_filename="demo.zip"
    )

    default_report = repository_council.run_repository_council(settings, manifest.repository_id)
    doc_heavy_report = repository_council.run_repository_council(
        settings, manifest.repository_id, weights={"documentation": 1.0}
    )

    assert default_report.overall_score != doc_heavy_report.overall_score


def test_run_and_record_council_persists_history(settings):
    manifest = rm.register_repository(
        _build_zip({"main.py": "x = 1\n"}), settings=settings, source_filename="demo.zip"
    )

    repository_council.run_and_record_council(settings, manifest.repository_id)
    repository_council.run_and_record_council(settings, manifest.repository_id)

    history = repository_council.get_council_history(settings, manifest.repository_id)
    assert len(history) == 2


def test_run_repository_council_raises_for_unknown_repository(settings):
    from app.services.repository_manager import RepositoryManagerError

    with pytest.raises(RepositoryManagerError):
        repository_council.run_repository_council(settings, "does-not-exist")
