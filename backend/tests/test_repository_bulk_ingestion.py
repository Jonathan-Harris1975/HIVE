from __future__ import annotations

import asyncio
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.api import repositories as repositories_api
from app.core.config import Settings, get_settings
from app.main import app
from app.services import repository_manager as rm


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "app_env": "test",
        "repository_temp_dir": str(tmp_path / "repos"),
        "repository_manager_enabled": True,
        "production_require_r2": False,
        "d1_enabled": False,
        "max_upload_bytes": 2 * 1024 * 1024,
        "repository_bulk_max_count": 8,
        "repository_bulk_max_total_bytes": 8 * 1024 * 1024,
        "repository_bulk_concurrency": 2,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _zip(repo_name: str, content: str = "VALUE = 1\n", *, traversal: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if traversal:
            archive.writestr("../escape.txt", "nope")
        else:
            archive.writestr(f"{repo_name}-main/app.py", content)
            archive.writestr(f"{repo_name}-main/README.md", f"# {repo_name}\n")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def clear_registry():
    rm._REGISTRY.clear()
    yield
    for repository_id in list(rm._REGISTRY):
        rm.cleanup_repository(repository_id)
    rm._REGISTRY.clear()


def _client(settings: Settings) -> TestClient:
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _ready_pipeline() -> dict[str, object]:
    return {
        "status": "ready",
        "required_stages_ready": True,
        "memory_seed": {"ok": True},
        "qa": {"ok": True},
        "council": {"ok": True},
        "intelligence": {"ok": True},
        "ai_search": {"ok": False, "skipped": True},
    }


def test_bulk_ingests_all_eight_governed_repositories(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(settings)

    async def fake_pipeline(*_args, **_kwargs):
        return _ready_pipeline()

    monkeypatch.setattr(repositories_api, "run_repository_pipeline", fake_pipeline)
    names = ["HIVE", "HIVE-UI", "AIMS", "AIMS-UI", "RAMS", "MAST", "IRS", "jonathan-harris-website"]
    files = [("uploads", (f"{name}-main.zip", _zip(name), "application/zip")) for name in names]

    response = client.post("/v1/repositories/bulk", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["failed_count"] == 0
    assert [item["repository_id"] for item in body["results"]] == [
        "HIVE", "HIVE-UI", "AIMS", "AIMS-UI", "RAMS", "MAST", "IRS", "Website"
    ]
    assert all(item["fingerprint"] for item in body["results"])


def test_bulk_mixed_failure_does_not_hide_success(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(settings)

    async def fake_pipeline(*_args, **_kwargs):
        return _ready_pipeline()

    monkeypatch.setattr(repositories_api, "run_repository_pipeline", fake_pipeline)
    response = client.post(
        "/v1/repositories/bulk",
        files=[
            ("uploads", ("HIVE-main.zip", _zip("HIVE"), "application/zip")),
            ("uploads", ("broken.zip", b"not a zip", "application/zip")),
            ("uploads", ("AIMS-main.zip", _zip("AIMS"), "application/zip")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert [item["outcome"] for item in body["results"]] == ["success", "failed", "success"]
    assert body["results"][0]["repository_id"] == "HIVE"
    assert body["results"][2]["repository_id"] == "AIMS"


def test_bulk_duplicate_and_updated_snapshot_outcomes(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    client = _client(settings)

    async def fake_pipeline(*_args, **_kwargs):
        return _ready_pipeline()

    monkeypatch.setattr(repositories_api, "run_repository_pipeline", fake_pipeline)
    original = _zip("HIVE", "VALUE = 1\n")
    first = client.post(
        "/v1/repositories/bulk",
        files=[
            ("uploads", ("HIVE-main.zip", original, "application/zip")),
            ("uploads", ("HIVE-copy.zip", original, "application/zip")),
        ],
    ).json()
    updated = client.post(
        "/v1/repositories/bulk",
        files=[("uploads", ("HIVE-main.zip", _zip("HIVE", "VALUE = 2\n"), "application/zip"))],
    ).json()

    assert first["results"][0]["outcome"] == "success"
    assert first["results"][1]["outcome"] == "duplicate"
    assert updated["results"][0]["outcome"] == "updated"
    assert updated["results"][0]["fingerprint"] != first["results"][0]["fingerprint"]


def test_bulk_rejects_traversal_and_oversized_batch(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, repository_bulk_max_total_bytes=300)
    client = _client(settings)

    async def fake_pipeline(*_args, **_kwargs):
        return _ready_pipeline()

    monkeypatch.setattr(repositories_api, "run_repository_pipeline", fake_pipeline)
    traversal = client.post(
        "/v1/repositories/bulk",
        files=[("uploads", ("HIVE-main.zip", _zip("HIVE", traversal=True), "application/zip"))],
    )
    oversized = client.post(
        "/v1/repositories/bulk",
        files=[
            ("uploads", ("HIVE-main.zip", _zip("HIVE", "x" * 500), "application/zip")),
            ("uploads", ("AIMS-main.zip", _zip("AIMS", "y" * 500), "application/zip")),
        ],
    )

    assert traversal.status_code == 200
    assert traversal.json()["results"][0]["outcome"] == "failed"
    assert oversized.status_code == 413


@pytest.mark.asyncio
async def test_bulk_honours_concurrency_limit(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path, repository_bulk_concurrency=2)
    active = 0
    peak = 0

    async def fake_ingest(content, filename, settings_arg, *, expected_repository_id=None):  # noqa: ANN001, ARG001
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        stem = filename.removesuffix("-main.zip")
        return {
            "repository_id": stem,
            "fingerprint": filename,
            "indexed_version": 1,
            "outcome": "success",
            "pipeline": _ready_pipeline(),
        }

    monkeypatch.setattr(repositories_api, "_ingest_repository_content", fake_ingest)
    app.dependency_overrides[get_settings] = lambda: settings
    # TestClient drives the async route from a sync context; using it here is fine.
    client = TestClient(app)
    files = [
        ("uploads", (f"repo-{i}-main.zip", _zip(f"repo-{i}"), "application/zip"))
        for i in range(6)
    ]
    response = client.post("/v1/repositories/bulk", files=files)

    assert response.status_code == 200
    assert peak == 2
