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


def test_repository_id_is_stable_for_governed_github_archives(settings):
    manifest = rm.register_repository(
        _build_zip({"HIVE-main/backend/app.py": "print('hi')\n"}),
        settings=settings,
        source_filename="HIVE-main.zip",
    )

    assert manifest.repository_id == "HIVE"
    record = rm.get_repository("HIVE")
    assert record is not None
    assert (record.workdir / "backend" / "app.py").exists()
    assert not (record.workdir / "HIVE-main").exists()




def test_github_api_zipball_wrapper_is_flattened_without_changing_expected_identity(settings):
    manifest = rm.register_repository(
        _build_zip({"Jonathan-Harris1975-HIVE-a1b2c3d/backend/app.py": "print('hi')\n"}),
        settings=settings,
        source_filename="HIVE-main.zip",
    )

    assert manifest.repository_id == "HIVE"
    record = rm.get_repository("HIVE")
    assert record is not None
    assert (record.workdir / "backend" / "app.py").exists()
    assert not (record.workdir / "Jonathan-Harris1975-HIVE-a1b2c3d").exists()


def test_website_archive_uses_governed_website_identity(settings):
    manifest = rm.register_repository(
        _build_zip({"jonathan-harris-website-main/package.json": '{"name":"website"}'}),
        settings=settings,
        source_filename="jonathan-harris-website-main.zip",
    )

    assert manifest.repository_id == "Website"


def test_monthly_upload_replaces_same_repository_and_increments_version(settings):
    first = rm.register_repository(
        _build_zip({"HIVE-main/main.py": "x = 1\n"}),
        settings=settings,
        source_filename="HIVE-main.zip",
    )
    second = rm.register_repository(
        _build_zip({"HIVE-main/main.py": "x = 2\n"}),
        settings=settings,
        source_filename="HIVE-main.zip",
    )

    assert first.repository_id == second.repository_id == "HIVE"
    assert second.indexed_version == first.indexed_version + 1
    assert len(rm.list_repositories()) == 1
    record = rm.get_repository("HIVE")
    assert record is not None
    assert (record.workdir / "main.py").read_text(encoding="utf-8") == "x = 2\n"


def test_restore_repository_snapshot_rebuilds_working_copy(settings, tmp_path):
    zip_bytes = _build_zip({"HIVE-main/main.py": "x = 1\n", "HIVE-main/README.md": "# HIVE\n"})
    manifest = rm.register_repository(zip_bytes, settings=settings, source_filename="HIVE-main.zip")
    rm.cleanup_repository("HIVE")

    archive_path = tmp_path / "snapshot.zip"
    archive_path.write_bytes(zip_bytes)
    record = rm.restore_repository_snapshot(archive_path, settings=settings, manifest=manifest)

    assert record.repository_id == "HIVE"
    assert record.workdir.exists()
    assert record.files_index
    assert record.manifest.fingerprint == manifest.fingerprint


def test_rehydrate_restores_snapshot_with_primary_r2_credentials(monkeypatch, settings):
    import json
    from types import SimpleNamespace
    from app.storage import r2 as r2_module

    zip_bytes = _build_zip({"HIVE-main/main.py": "x = 1\n"})
    original = rm.register_repository(zip_bytes, settings=settings, source_filename="HIVE-main.zip")
    payload = original.public_payload()
    rm.cleanup_repository("HIVE")

    class FakeR2:
        write_enabled = True
        read_enabled = False

        def __init__(self, _settings):
            pass

        def list_objects(self, *, prefix, limit, bucket, read_only):
            assert prefix == "manifests/"
            assert read_only is False
            return [SimpleNamespace(key="manifests/HIVE.json")]

        def read_object(self, key, max_bytes, *, bucket, read_only):
            assert key == "manifests/HIVE.json"
            assert read_only is False
            return SimpleNamespace(content=json.dumps(payload).encode("utf-8"))

        def open_object(self, key, *, bucket, max_bytes, read_only):
            assert key == "snapshots/HIVE.zip"
            assert read_only is False
            return SimpleNamespace(body=io.BytesIO(zip_bytes))

    monkeypatch.setattr(r2_module, "R2Storage", FakeR2)
    settings.r2_bucket_repositories = "repositories"

    assert rm.rehydrate_registry_from_r2(settings) == 1
    record = rm.get_repository("HIVE")
    assert record is not None
    assert not rm.is_rehydrated(record)
    assert (record.workdir / "main.py").exists()



def test_rehydrate_falls_back_to_write_credentials_when_read_credentials_cannot_access_repository_bucket(
    monkeypatch, settings
):
    import json
    from types import SimpleNamespace
    from app.storage import r2 as r2_module

    zip_bytes = _build_zip({"HIVE-main/main.py": "x = 1\n"})
    original = rm.register_repository(zip_bytes, settings=settings, source_filename="HIVE-main.zip")
    payload = original.public_payload()
    rm.cleanup_repository("HIVE")

    calls: list[tuple[str, bool]] = []

    class FakeR2:
        write_enabled = True
        read_enabled = True

        def __init__(self, _settings):
            pass

        def list_objects(self, *, prefix, limit, bucket, read_only):
            calls.append(("list", read_only))
            if read_only:
                raise RuntimeError("read token does not include repository bucket")
            return [SimpleNamespace(key="manifests/HIVE.json")]

        def read_object(self, key, max_bytes, *, bucket, read_only):
            calls.append(("manifest", read_only))
            if read_only:
                raise RuntimeError("read token does not include repository bucket")
            return SimpleNamespace(content=json.dumps(payload).encode("utf-8"))

        def open_object(self, key, *, bucket, max_bytes, read_only):
            calls.append(("snapshot", read_only))
            if read_only:
                raise RuntimeError("read token does not include repository bucket")
            return SimpleNamespace(body=io.BytesIO(zip_bytes))

    monkeypatch.setattr(r2_module, "R2Storage", FakeR2)
    settings.r2_bucket_repositories = "repositories"

    assert rm.rehydrate_registry_from_r2(settings) == 1
    record = rm.get_repository("HIVE")
    assert record is not None
    assert not rm.is_rehydrated(record)
    assert calls[:2] == [("list", True), ("list", False)]
    assert ("manifest", False) in calls
    assert ("snapshot", False) in calls

def test_repository_id_prefers_github_wrapper_when_upload_filename_is_renamed(settings):
    manifest = rm.register_repository(
        _build_zip({"HIVE-main/backend/app.py": "print('hi')\n"}),
        settings=settings,
        source_filename="august-business-snapshot.zip",
    )

    assert manifest.repository_id == "HIVE"
    assert rm.get_repository("HIVE") is not None


def test_rehydrate_uses_newest_legacy_manifest_for_stable_repository(monkeypatch, settings):
    import json
    from types import SimpleNamespace
    from app.storage import r2 as r2_module

    older_zip = _build_zip({"HIVE-main/main.py": "x = 1\n"})
    newer_zip = _build_zip({"HIVE-main/main.py": "x = 2\n"})

    older_manifest = rm.register_repository(
        older_zip, settings=settings, source_filename="HIVE-main.zip"
    )
    rm.cleanup_repository("HIVE")
    newer_manifest = rm.register_repository(
        newer_zip, settings=settings, source_filename="HIVE-main.zip"
    )
    rm.cleanup_repository("HIVE")

    old_id = "1" * 32
    new_id = "2" * 32
    older_payload = {**older_manifest.public_payload(), "repository_id": old_id, "updated_at": 1.0}
    newer_payload = {**newer_manifest.public_payload(), "repository_id": new_id, "updated_at": 2.0}

    class FakeR2:
        write_enabled = False
        read_enabled = True

        def __init__(self, _settings):
            pass

        def list_objects(self, *, prefix, limit, bucket, read_only):
            assert read_only is True
            return [
                SimpleNamespace(key=f"manifests/{old_id}.json", last_modified="2026-07-01T00:00:00Z"),
                SimpleNamespace(key=f"manifests/{new_id}.json", last_modified="2026-08-01T00:00:00Z"),
            ]

        def read_object(self, key, max_bytes, *, bucket, read_only):
            payload = newer_payload if new_id in key else older_payload
            return SimpleNamespace(content=json.dumps(payload).encode("utf-8"))

        def open_object(self, key, *, bucket, max_bytes, read_only):
            if key == f"snapshots/{new_id}.zip":
                return SimpleNamespace(body=io.BytesIO(newer_zip))
            if key == f"snapshots/{old_id}.zip":
                return SimpleNamespace(body=io.BytesIO(older_zip))
            raise RuntimeError("not found")

    monkeypatch.setattr(r2_module, "R2Storage", FakeR2)
    settings.r2_bucket_repositories = "repositories"

    assert rm.rehydrate_registry_from_r2(settings) == 1
    record = rm.get_repository("HIVE")
    assert record is not None
    assert record.manifest.fingerprint == newer_manifest.fingerprint
    assert (record.workdir / "main.py").read_text(encoding="utf-8") == "x = 2\n"


def test_pyproject_dependencies_are_indexed_for_python_architecture(settings):
    archive = _build_zip(
        {
            "HIVE-main/pyproject.toml": """
[project]
name = "hive"
dependencies = ["fastapi>=0.141", "httpx>=0.28"]
[project.optional-dependencies]
dev = ["pytest>=9"]
""",
            "HIVE-main/backend/app.py": "print('ok')\n",
        }
    )
    manifest = rm.register_repository(archive, settings=settings, source_filename="HIVE-main.zip")

    pyproject = next(dep for dep in manifest.dependencies if dep.manifest_path == "pyproject.toml")
    assert pyproject.ecosystem == "python"
    assert pyproject.declared == ["fastapi", "httpx", "pytest"]
