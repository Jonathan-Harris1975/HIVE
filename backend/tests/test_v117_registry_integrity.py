from __future__ import annotations

from app.core.version import BUILD_STAGE
from app.services import skill_registry as registry


class _SettingsStub:
    def public_url_for_r2_lane(self, lane: str, key: str) -> str:
        base = "https://skills.example.test"
        return f"{base}/{key}" if key else base


SETTINGS = _SettingsStub()


def _valid_item(skill_id: str = "S194", slug: str = "podcast-seo") -> dict[str, object]:
    return {
        "id": f"skill:{skill_id}",
        "lane": "hive_skills",
        "source_type": "skill_descriptor",
        "source_id": skill_id,
        "title": slug,
        "url": f"https://skills.example.test/skills/{skill_id}_{slug}.json",
        "metadata": {
            "skill_id": skill_id,
            "reference_prefix": skill_id,
            "slug": slug,
            "name": slug,
            "object_key": f"skills/{skill_id}_{slug}.json",
            "descriptor_url": f"https://skills.example.test/skills/{skill_id}_{slug}.json",
            "search_document_id": f"skill:{skill_id}",
            "priority_tier": "P0 - Foundation",
            "hive_lane": "SEO/AEO/GEO",
            "risk_level": "low",
            "repos": ["HIVE", "AIMS"],
            "tags": ["podcast-seo", "repo-hive", "risk-low"],
            "catalogue_category": "content-operations",
            "indexable_text": "Podcast SEO skill for transcript and RSS review.",
        },
    }


def test_v117_build_marker() -> None:
    assert BUILD_STAGE == "v1.26-r2-write-skill-models"


def test_v117_integrity_report_clean_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        registry,
        "_skill_records",
        lambda **kwargs: {"ok": True, "items": [_valid_item()]},
    )

    result = registry.skill_registry_integrity_report(settings=SETTINGS)

    assert result["ok"] is True
    assert result["build_stage_hint"] == "v1.26-r2-write-skill-models"
    assert result["checked_count"] == 1
    assert result["issue_count"] == 0
    assert result["registry_health"] == 100


def test_v117_duplicate_and_missing_detection(monkeypatch) -> None:
    first = _valid_item("S194", "podcast-seo")
    duplicate = _valid_item("S194", "podcast-seo-copy")
    broken = _valid_item("S999", "broken-skill")
    broken["metadata"] = {
        "skill_id": "S999",
        "slug": "broken-skill",
        "object_key": "skills/S999_broken-skill.json",
        "descriptor_url": "https://wrong.example.test/skills/S999_broken-skill.json",
        "priority_tier": "P9 - Weird",
        "risk_level": "extreme",
        "repos": ["UnknownRepo"],
    }
    monkeypatch.setattr(
        registry,
        "_skill_records",
        lambda **kwargs: {"ok": True, "items": [first, duplicate, broken]},
    )

    result = registry.skill_registry_integrity_report(settings=SETTINGS)

    assert result["ok"] is True
    assert result["issue_count"] > 0
    assert result["duplicates"]["skill_ids"][0]["value"] == "s194"
    assert result["missing"]["count"] == 1
    assert result["taxonomy"]["count"] == 1
    assert result["orphans"]["count"] == 1


def test_v117_rebuild_index_defaults_to_dry_run(monkeypatch) -> None:
    called = {}

    def fake_import_skills_manifest(**kwargs):
        called.update(kwargs)
        return {"ok": True, "dry_run": kwargs["dry_run"], "prepared_count": 201}

    monkeypatch.setattr(registry, "import_skills_manifest", fake_import_skills_manifest)

    result = registry.rebuild_skills_index(settings=SETTINGS)

    assert result["ok"] is True
    assert result["operation"] == "rebuild_skills_index"
    assert called["dry_run"] is True
