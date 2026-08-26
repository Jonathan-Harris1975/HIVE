from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import repositories, repository_learning, repository_memory


@pytest.mark.asyncio
async def test_repository_setup_repairs_existing_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = SimpleNamespace(public_payload=lambda: {"repository_id": "HIVE"})
    record = SimpleNamespace(manifest=manifest)
    monkeypatch.setattr(repositories, "get_repository", lambda repository_id: record if repository_id == "HIVE" else None)
    monkeypatch.setattr(repositories, "is_rehydrated", lambda _record: False)
    monkeypatch.setattr(repositories, "_persist_manifest_to_r2", lambda *_args, **_kwargs: True)

    async def pipeline(_settings, _manifest, *, r2_persisted: bool):
        assert r2_persisted is True
        return {
            "repository_id": "HIVE",
            "status": "ready",
            "required_stages_ready": True,
        }

    monkeypatch.setattr(repositories, "run_repository_pipeline", pipeline)
    result = await repositories.post_repository_setup(
        "HIVE",
        settings=SimpleNamespace(production_require_r2=True),
    )

    assert result["repository_id"] == "HIVE"
    assert result["ready"] is True
    assert result["pipeline"]["status"] == "ready"


@pytest.mark.asyncio
async def test_repository_setup_rejects_unknown_and_legacy_snapshotless_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repositories, "get_repository", lambda _repository_id: None)
    with pytest.raises(HTTPException) as missing:
        await repositories.post_repository_setup(
            "missing",
            settings=SimpleNamespace(production_require_r2=False),
        )
    assert missing.value.status_code == 404

    record = SimpleNamespace(manifest=SimpleNamespace())
    monkeypatch.setattr(repositories, "get_repository", lambda _repository_id: record)
    monkeypatch.setattr(repositories, "is_rehydrated", lambda _record: True)
    with pytest.raises(HTTPException) as legacy:
        await repositories.post_repository_setup(
            "HIVE",
            settings=SimpleNamespace(production_require_r2=False),
        )
    assert legacy.value.status_code == 409
    assert "Re-upload" in str(legacy.value.detail)


@pytest.mark.asyncio
async def test_repository_setup_fails_closed_when_r2_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = SimpleNamespace(public_payload=lambda: {"repository_id": "HIVE"})
    record = SimpleNamespace(manifest=manifest)
    monkeypatch.setattr(repositories, "get_repository", lambda _repository_id: record)
    monkeypatch.setattr(repositories, "is_rehydrated", lambda _record: False)
    monkeypatch.setattr(repositories, "_persist_manifest_to_r2", lambda *_args, **_kwargs: False)

    with pytest.raises(HTTPException) as exc:
        await repositories.post_repository_setup(
            "HIVE",
            settings=SimpleNamespace(production_require_r2=True),
        )
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_repository_memory_rejects_ghost_repository_before_d1_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_memory, "get_repository", lambda _repository_id: None)
    with pytest.raises(HTTPException) as exc:
        await repository_memory.get_memory("ghost", settings=SimpleNamespace())
    assert exc.value.status_code == 404
    assert "ghost" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_repository_learning_rejects_ghost_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_learning, "get_repository", lambda _repository_id: None)
    body = repository_learning.CodingPatternRequest(pattern="Use apiFetch")
    with pytest.raises(HTTPException) as exc:
        await repository_learning.post_coding_pattern(
            "ghost",
            body=body,
            settings=SimpleNamespace(),
        )
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_upload_pipeline_populates_memory_and_intelligence_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import io
    import zipfile

    from fastapi import UploadFile

    from app.api import repository_memory as repository_memory_api
    from app.services import repository_council, repository_intelligence, repository_learning, repository_manager
    from app.storage import ai_search, d1 as d1_storage

    repository_manager._REGISTRY.clear()

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(
            "HIVE-main/pyproject.toml",
            '[project]\nname="hive"\ndependencies=["fastapi>=0.141"]\n',
        )
        zipped.writestr("HIVE-main/backend/app.py", "from fastapi import FastAPI\n")
        zipped.writestr("HIVE-main/README.md", "# HIVE\n")
        zipped.writestr("HIVE-main/.env.example", "DATABASE_URL=\nD1_DATABASE_ID=\n")

    class FakeD1:
        enabled = True
        rows: dict[str, dict[str, object]] = {}

        def __init__(self, _settings) -> None:
            pass

        def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
            self.rows[item_id] = {
                "id": item_id,
                "lane": lane,
                "source_type": source_type,
                "source_id": source_id,
                "title": title,
                "url": url,
                "metadata": metadata,
                "updated_at": "2026-08-26T18:00:00+00:00",
            }
            return {"ok": True, "enabled": True}

        def list_metadata(self, *, lane=None, limit=500):
            items = [
                row for row in self.rows.values()
                if lane is None or row["lane"] == lane
            ]
            return {"ok": True, "enabled": True, "items": items[:limit], "count": len(items)}

        def search_metadata(self, *, query, lane=None, limit=50):
            items = [
                row for row in self.rows.values()
                if (lane is None or row["lane"] == lane) and query.lower() in str(row).lower()
            ]
            return {"ok": True, "enabled": True, "items": items[:limit], "count": len(items)}

        def delete_metadata_ids(self, item_ids):
            for item_id in item_ids:
                self.rows.pop(item_id, None)
            return {"ok": True}

    class FakeR2:
        write_enabled = True

        def __init__(self, _settings) -> None:
            pass

        def put_file(self, *_args, **_kwargs):
            return {"ok": True}

    class DisabledSearch:
        enabled = False

        def __init__(self, _settings) -> None:
            pass

    FakeD1.rows.clear()
    monkeypatch.setattr(d1_storage, "D1MetadataStore", FakeD1)
    monkeypatch.setattr(repositories, "R2Storage", FakeR2)
    monkeypatch.setattr(repositories, "D1MetadataStore", FakeD1)
    monkeypatch.setattr(repository_memory_api, "D1MetadataStore", FakeD1)
    monkeypatch.setattr(repository_council, "D1MetadataStore", FakeD1)
    monkeypatch.setattr(repository_intelligence, "D1MetadataStore", FakeD1)
    monkeypatch.setattr(repository_learning, "D1MetadataStore", FakeD1)
    monkeypatch.setattr(ai_search, "AiSearchClient", DisabledSearch)

    settings = SimpleNamespace(
        repository_manager_enabled=True,
        repository_temp_dir=str(tmp_path),
        repository_max_files=20_000,
        repository_max_uncompressed_bytes=64 * 1024 * 1024,
        r2_bucket_repositories="repositories",
        production_require_r2=False,
    )
    upload = UploadFile(file=io.BytesIO(archive.getvalue()), filename="HIVE-main.zip")

    payload = await repositories.upload_repository(upload=upload, settings=settings)
    listed = await repositories.get_repositories(settings=settings)
    memory = await repository_memory_api.get_memory("HIVE", settings=settings)

    assert payload["repository_id"] == "HIVE"
    assert payload["pipeline"]["status"] == "ready"
    assert payload["pipeline"]["required_stages_ready"] is True
    assert listed["repositories"][0]["memory_ready"] is True
    assert memory["memory"]["project_manifest"]["repository_id"] == "HIVE"
    assert "FastAPI" in memory["memory"]["architecture_summary"]["architecture_signals"]
    assert memory["memory"]["project_dna"]["latest_qa_score"] is not None
    assert memory["memory"]["project_dna"]["latest_council_score"] is not None

    repository_manager.cleanup_repository("HIVE")
