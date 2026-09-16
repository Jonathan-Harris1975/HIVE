from __future__ import annotations

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.services import skill_registry as registry

SETTINGS = Settings(APP_ENV="test")


def _valid_item(skill_id: str = "HIVE-sk001", slug: str = "ci-log-analysis") -> dict[str, object]:
    source_uri = f"repo://skills/catalogue_metadata.json#{skill_id}"
    return {
        "id": f"skill:{skill_id}",
        "lane": registry.SKILL_LANE,
        "source_type": "repository_skill",
        "source_id": skill_id,
        "title": slug,
        "url": source_uri,
        "metadata": {
            "skill_id": skill_id,
            "slug": slug,
            "description": "Local test capability.",
            "priority_tier": "P0 - Foundation",
            "hive_lane": "CI and deployment diagnostics",
            "risk_level": "low",
            "repos": ["HIVE"],
            "tags": ["ci"],
            "catalogue_category": "CI and deployment diagnostics",
            "indexable_text": "Local CI diagnostic capability.",
            "source_path": registry.CATALOGUE_PATH,
            "source_uri": source_uri,
            "implementation_paths": ["backend/app/services/repo_health.py"],
            "external_content_copied": False,
        },
    }


def test_v117_build_marker() -> None:
    assert BUILD_STAGE == "v1.31-production-readiness"


def test_v117_integrity_report_clean_registry() -> None:
    result = registry.skill_registry_integrity_report(settings=SETTINGS)

    assert result["ok"] is True
    assert result["checked_count"] > 0
    assert result["issue_count"] == 0
    assert result["registry_health"] == 100
    assert result["external_dependencies"] == []


def test_v117_duplicate_and_missing_detection(monkeypatch) -> None:
    first = _valid_item()
    duplicate = _valid_item("HIVE-sk001", "ci-log-analysis-copy")
    broken = _valid_item("HIVE-sk999", "broken-skill")
    broken["metadata"] = {
        "skill_id": "HIVE-sk999",
        "slug": "broken-skill",
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
    assert result["duplicates"]["skill_ids"][0]["value"] == "hive-sk001"
    assert result["missing"]["count"] == 1
    assert result["taxonomy"]["count"] == 1
    assert result["orphans"]["count"] == 1


def test_v117_rebuild_index_reloads_local_catalogue() -> None:
    result = registry.rebuild_skills_index(settings=SETTINGS)

    assert result["ok"] is True
    assert result["operation"] == "reload_local_skills_catalogue"
    assert result["mutated_external_state"] is False
