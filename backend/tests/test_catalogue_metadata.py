from app.services.catalogue_metadata import (
    catalogue_status,
    enrich_skill_item,
    enrich_task_item,
    load_skill_catalogue_metadata,
    load_task_catalogue_metadata,
)


def test_catalogue_metadata_files_are_valid() -> None:
    skills = load_skill_catalogue_metadata()
    tasks = load_task_catalogue_metadata()

    assert skills["schema_version"] == "2026-09-15.repository-skills.v1"
    assert tasks["schema_version"] == "2026-06-22.catalogue-metadata.v1"
    assert len(skills["items"]) == 8
    assert len(tasks["items"]) >= 18

    status = catalogue_status()
    assert status["ok"] is True
    assert status["skills"]["missing_required_count"] == 0
    assert status["tasks"]["missing_required_count"] == 0


def test_skill_enrichment_prevents_blank_description() -> None:
    item = {
        "id": "skill:ci_log_analysis",
        "source_id": "ci_log_analysis",
        "title": "CI/log analysis",
        "metadata": {"tags": ["ci", "logs"], "repos": ["HIVE"]},
    }

    enriched = enrich_skill_item(item)

    assert enriched["description"]
    assert enriched["metadata"]["description"] == enriched["description"]
    assert enriched["metadata"]["metadata_source"] == "skills/catalogue_metadata.json"
    assert enriched["risk_level"] in {"low", "medium", "high"}


def test_unknown_skill_uses_safe_local_default_metadata() -> None:
    item = {
        "id": "skill:future-koyeb-log-helper",
        "source_id": "future-koyeb-log-helper",
        "title": "Future Koyeb log helper",
        "metadata": {"tags": ["koyeb", "logs"], "repos": ["HIVE"]},
    }

    enriched = enrich_skill_item(item)

    assert enriched["description"] == (
        "Supports operations for HIVE using governed skill metadata and review-gated planning."
    )
    assert enriched["category"] == "General operations"


def test_task_enrichment_prevents_blank_description() -> None:
    enriched = enrich_task_item({"id": "adapter_execution", "label": "Production adapter handoff"})

    assert enriched["description"].startswith("Hands an approved plan")
    assert enriched["requires_approval"] is True
    assert enriched["metadata_source"] == "tasks/task_metadata.json"
