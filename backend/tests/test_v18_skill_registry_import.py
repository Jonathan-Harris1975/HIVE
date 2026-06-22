from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.services.skill_registry import _extract_search_documents, _skill_document_to_metadata, _skill_stats_from_items


def _reset_settings(monkeypatch, tmp_path, **env):
    from app.core.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("D1_ENABLED", "false")
    monkeypatch.setenv("R2_BUCKET_HIVE_SKILLS", "hive-skills")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL_HIVE_SKILLS", "https://skills.example.test")
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


def test_skills_status_exposes_v18_manifest_hints(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    body = client.get("/v1/skills/status").json()

    assert body["ok"] is True
    assert body["build_stage_hint"] == "v1.26.12-catalogue-metadata"
    assert body["search_documents_url"] == "https://skills.example.test/index/search-documents.json"
    assert body["shared_manifest_url"] == "https://skills.example.test/manifests/shared-skill-pool-manifest.json"


def test_import_manifest_is_safe_when_d1_disabled(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    body = client.post("/v1/skills/import-manifest", json={"dry_run": True}).json()

    assert body["ok"] is False
    assert body["enabled"] is False
    assert body["error_code"] == "d1_disabled"
    assert body["search_documents_hint"] == "https://skills.example.test/index/search-documents.json"


def test_skill_document_mapping_preserves_categories(monkeypatch, tmp_path) -> None:
    _reset_settings(monkeypatch, tmp_path)
    from app.core.config import get_settings

    settings = get_settings()
    doc = {
        "document_id": "skill:S999",
        "reference_prefix": "S999",
        "name": "seo-audit-helper",
        "object_key": "skills/S999_seo-audit-helper.json",
        "text": "SEO audit helper for RAMS and AIMS.",
        "metadata": {
            "skill_id": "S999",
            "reference_prefix": "S999",
            "slug": "seo-audit-helper",
            "priority_tier": "P1 - High",
            "hive_lane": "SEO/AEO/GEO",
            "risk_level": "low",
            "repos": ["HIVE", "RAMS", "AIMS"],
        },
        "tags": ["seo", "audit", "repo-rams"],
    }

    mapped = _skill_document_to_metadata(settings, doc)
    meta = mapped["metadata"]

    assert mapped["id"] == "skill:S999"
    assert meta["priority_tier"] == "P1 - High"
    assert meta["hive_lane"] == "SEO/AEO/GEO"
    assert meta["risk_level"] == "low"
    assert meta["catalogue_category"] == "content-operations"
    assert "RAMS" in meta["repos"]
    assert meta["descriptor_url"] == "https://skills.example.test/skills/S999_seo-audit-helper.json"


def test_extract_search_documents_and_stats() -> None:
    docs = _extract_search_documents({"documents": [
        {"name": "one", "metadata": {"priority_tier": "P0", "hive_lane": "Core", "risk_level": "low", "repos": ["HIVE"]}},
        {"name": "two", "metadata": {"priority_tier": "P1", "hive_lane": "Audit", "risk_level": "medium", "repos": ["RAMS"]}},
    ]})

    assert len(docs) == 2
    prepared = [
        {"priority_tier": "P0", "hive_lane": "Core", "risk_level": "low", "repos": ["HIVE"], "catalogue_category": "skill-governance"},
        {"priority_tier": "P1", "hive_lane": "Audit", "risk_level": "medium", "repos": ["RAMS"], "catalogue_category": "risk-and-audit"},
    ]
    stats = _skill_stats_from_items(prepared)
    assert stats["count"] == 2
    assert stats["by_repo"]["HIVE"] == 1
    assert stats["by_repo"]["RAMS"] == 1
