from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.services.skill_registry import _skill_stats_from_items


def _reset_settings(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()
    monkeypatch.setitem(app.dependency_overrides, get_settings, get_settings)


def test_skills_status_exposes_local_catalogue(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    body = TestClient(app).get("/v1/skills/status").json()

    assert body["ok"] is True
    assert body["build_stage_hint"] == "v1.31-production-readiness"
    assert body["access_mode"] == "repository-local-read-only"
    assert body["source_of_truth"] == "skills/catalogue_metadata.json"
    assert body["shared_bucket_required"] is False
    assert body["indexed_skill_count"] > 0


def test_remote_manifest_import_route_is_removed(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    response = TestClient(app).post("/v1/skills/import-manifest", json={"dry_run": True})

    assert response.status_code == 404


def test_local_catalogue_records_are_prefixed_and_native(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    body = TestClient(app).get("/v1/skills/list", params={"limit": 50}).json()

    assert body["ok"] is True
    assert body["source"] == "repo://skills/catalogue_metadata.json"
    assert all(item["source_id"].startswith("HIVE-sk") for item in body["items"])
    assert all(item["source_type"] == "repository_skill" for item in body["items"])
    assert all(item["metadata"]["external_content_copied"] is False for item in body["items"])


def test_skill_stats_remain_deterministic() -> None:
    prepared = [
        {
            "priority_tier": "P0 - Foundation",
            "hive_lane": "Core",
            "risk_level": "low",
            "repos": ["HIVE"],
            "catalogue_category": "skill-governance",
        },
        {
            "priority_tier": "P1 - High",
            "hive_lane": "Audit",
            "risk_level": "medium",
            "repos": ["RAMS"],
            "catalogue_category": "risk-and-audit",
        },
    ]

    stats = _skill_stats_from_items(prepared)

    assert stats["count"] == 2
    assert stats["by_repo"] == {"HIVE": 1, "RAMS": 1}
