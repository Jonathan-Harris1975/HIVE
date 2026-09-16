from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _reset_settings(monkeypatch, tmp_path, **env):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'hive.sqlite3'}")
    monkeypatch.setenv("D1_ENABLED", "false")
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()
    monkeypatch.setitem(app.dependency_overrides, get_settings, get_settings)


def test_ecosystem_status_is_mast_friendly(monkeypatch, tmp_path) -> None:
    _reset_settings(
        monkeypatch,
        tmp_path,
        VECTORIZE_ENABLED="true",
        VECTORIZE_API_TOKEN="token",
        VECTORIZE_ACCOUNT_ID="account",
        EMBEDDINGS_ENABLED="true",
        EMBEDDINGS_API_TOKEN="token",
        EMBEDDINGS_ACCOUNT_ID="account",
    )
    client = TestClient(app)

    body = client.get("/v1/ecosystem/status").json()

    assert body["ok"] is True
    assert body["build_stage_hint"] == "v1.31-production-readiness"
    assert body["services"]["skills"]["configured"] is True
    assert body["services"]["vectorize"]["configured"] is True
    assert body["recommended_mast_probe"] == "/v1/ecosystem/status"


def test_skills_list_and_search_use_repository_catalogue(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    listed = client.get("/v1/skills/list").json()
    searched = client.get("/v1/skills/search", params={"q": "audit"}).json()

    assert listed["ok"] is True
    assert listed["items"]
    assert listed["shared_bucket_required"] is False
    assert searched["ok"] is True
    assert searched["items"]
    assert searched["catalogue_path"] == "skills/catalogue_metadata.json"


def test_ecosystem_search_requires_query(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/v1/ecosystem/search")

    assert response.status_code == 422


def test_r2_discovery_handles_unconfigured_storage_without_crashing(monkeypatch, tmp_path) -> None:
    _reset_settings(
        monkeypatch,
        tmp_path,
        R2_BUCKET_AUDITS="audits",
    )
    client = TestClient(app)

    body = client.get("/v1/files/r2-discovery", params={"lane": "audits", "limit": 5}).json()

    assert body["ok"] is True
    assert body["count"] == 1
    assert body["discoveries"][0]["lane"] == "audits"
    assert body["discoveries"][0]["ok"] is False
