from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def _reset_settings(monkeypatch, tmp_path, **env):
    from app.core.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'hive.sqlite3'}")
    monkeypatch.setenv("D1_ENABLED", "false")
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


def test_ecosystem_status_is_mast_friendly(monkeypatch, tmp_path) -> None:
    _reset_settings(
        monkeypatch,
        tmp_path,
        R2_BUCKET_HIVE_SKILLS="hive-skills",
        R2_PUBLIC_BASE_URL_HIVE_SKILLS="https://skills.example.test",
        SKILL_REGISTRY_FALLBACK_ENABLED=False,
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
    assert body["build_stage_hint"] == "v1.26.1-file-to-skill-review-flow"
    assert body["services"]["skills"]["configured"] is True
    assert body["services"]["vectorize"]["configured"] is True
    assert body["recommended_mast_probe"] == "/v1/ecosystem/status"


def test_skills_list_and_search_return_safe_disabled_d1_response(monkeypatch, tmp_path) -> None:
    _reset_settings(
        monkeypatch,
        tmp_path,
        R2_BUCKET_HIVE_SKILLS="hive-skills",
        R2_PUBLIC_BASE_URL_HIVE_SKILLS="https://skills.example.test",
        SKILL_REGISTRY_FALLBACK_ENABLED=False,
    )
    client = TestClient(app)

    listed = client.get("/v1/skills/list").json()
    searched = client.get("/v1/skills/search", params={"q": "audit"}).json()

    assert listed["ok"] is False
    assert listed["enabled"] is False
    assert listed["manifest_hint"] == "https://skills.example.test/index/skills-manifest.json"
    assert searched["ok"] is False
    assert searched["enabled"] is False
    assert searched["manifest_hint"] == "https://skills.example.test/index/skills-manifest.json"


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
        R2_PUBLIC_BASE_URL_AUDITS="https://audits.example.test",
    )
    client = TestClient(app)

    body = client.get("/v1/files/r2-discovery", params={"lane": "audits", "limit": 5}).json()

    assert body["ok"] is True
    assert body["count"] == 1
    assert body["discoveries"][0]["lane"] == "audits"
    assert body["discoveries"][0]["ok"] is False
