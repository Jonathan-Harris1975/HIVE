from app.services.catalogue_metadata import (
    catalogue_status,
    enrich_task_item,
    load_task_catalogue_metadata,
)


def test_task_catalogue_metadata_file_is_valid() -> None:
    tasks = load_task_catalogue_metadata()

    assert tasks["schema_version"] == "2026-06-22.catalogue-metadata.v1"
    assert len(tasks["items"]) >= 17

    status = catalogue_status()
    assert status["ok"] is True
    assert status["tasks"]["missing_required_count"] == 0


def test_task_enrichment_prevents_blank_description() -> None:
    enriched = enrich_task_item({"id": "adapter_execution", "label": "Production adapter handoff"})

    assert enriched["description"].startswith("Hands an approved plan")
    assert enriched["requires_approval"] is True
    assert enriched["metadata_source"] == "tasks/task_metadata.json"
