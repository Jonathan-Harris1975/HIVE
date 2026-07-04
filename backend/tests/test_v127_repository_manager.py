import io
import time
import zipfile

import pytest

from app.core.config import Settings
from app.services import repository_manager as rm


def _build_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _isolated_registry():
    rm._REGISTRY.clear()
    yield
    for repository_id in list(rm._REGISTRY.keys()):
        rm.cleanup_repository(repository_id)


@pytest.fixture
def settings(tmp_path):
    return Settings(REPOSITORY_TEMP_DIR=str(tmp_path))


def test_register_repository_extracts_fingerprints_and_detects_language(settings):
    zip_bytes = _build_zip(
        {
            "main.py": "print('hi')\n",
            "requirements.txt": "fastapi==0.111.0\nboto3>=1.34\n# comment\n",
            "pkg/util.js": "console.log('x');\n",
        }
    )

    manifest = rm.register_repository(zip_bytes, settings=settings, source_filename="demo.zip")

    assert manifest.file_count == 3
    assert manifest.languages.get("Python") == 1
    assert manifest.languages.get("JavaScript") == 1
    assert manifest.indexed_version == 1
    assert len(manifest.fingerprint) == 64

    dep = next(d for d in manifest.dependencies if d.manifest_path == "requirements.txt")
    assert dep.ecosystem == "pip"
    assert "fastapi" in dep.declared
    assert "boto3" in dep.declared

    record = rm.get_repository(manifest.repository_id)
    assert record is not None
    assert record.workdir.exists()


def test_register_repository_rejects_unsafe_zip(settings):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.txt", "nope")

    with pytest.raises(rm.RepositoryManagerError):
        rm.register_repository(buffer.getvalue(), settings=settings, source_filename="bad.zip")


def test_reindex_detects_added_changed_and_removed_files(settings):
    zip_bytes = _build_zip({"a.py": "x = 1\n", "b.py": "y = 2\n"})
    manifest = rm.register_repository(zip_bytes, settings=settings, source_filename="demo.zip")
    record = rm.get_repository(manifest.repository_id)
    assert record is not None

    (record.workdir / "a.py").write_text("x = 2\n", encoding="utf-8")
    (record.workdir / "b.py").unlink()
    (record.workdir / "c.py").write_text("z = 3\n", encoding="utf-8")

    diff = rm.repository_diff(manifest.repository_id)
    assert diff == {"added": ["c.py"], "removed": ["b.py"], "changed": ["a.py"]}

    updated = rm.reindex_repository(manifest.repository_id)
    assert updated.indexed_version == manifest.indexed_version + 1
    assert updated.file_count == 2


def test_list_repositories_and_cleanup(settings):
    manifest = rm.register_repository(
        _build_zip({"a.py": "1\n"}), settings=settings, source_filename="demo.zip"
    )
    summaries = rm.list_repositories()
    assert any(item.repository_id == manifest.repository_id for item in summaries)

    removed = rm.cleanup_repository(manifest.repository_id)
    assert removed is True
    assert rm.get_repository(manifest.repository_id) is None
    assert rm.cleanup_repository(manifest.repository_id) is False


def test_cleanup_expired_repositories_removes_only_idle_entries(settings):
    manifest = rm.register_repository(
        _build_zip({"a.py": "1\n"}), settings=settings, source_filename="demo.zip"
    )
    record = rm.get_repository(manifest.repository_id)
    assert record is not None
    record.last_accessed_at = time.time() - 999_999

    removed = rm.cleanup_expired_repositories(ttl_seconds=10)

    assert removed == [manifest.repository_id]
    assert rm.get_repository(manifest.repository_id) is None
