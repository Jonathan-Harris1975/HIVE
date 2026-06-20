from __future__ import annotations

from app.core.config import Settings
from app.services import skill_registry as registry


class _FakeD1:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.deleted: list[str] = []

    @property
    def enabled(self) -> bool:
        return True

    def safe_config(self) -> dict[str, object]:
        return {"enabled": True}

    def list_metadata(self, *, lane: str | None = None, limit: int = 50) -> dict[str, object]:
        assert lane == "hive_skills"
        return {
            "ok": True,
            "items": [
                {
                    "id": "skill:upload-badpng",
                    "lane": "hive_skills",
                    "source_type": "skill_descriptor",
                    "source_id": "upload-badpng",
                    "title": "blog-fallback-hero.png",
                    "metadata": {
                        "skill_id": "upload-badpng",
                        "name": "blog-fallback-hero.png",
                        "object_key": "blog-fallback-hero.png",
                        "source_lane": "brand_assets",
                        "source_object_key": "blog-fallback-hero.png",
                        "hive_lane": "uploaded-file-skills",
                        "tags": ["uploaded-file", "image"],
                        "source_register": "HIVE direct file upload",
                        "created_from_file": True,
                    },
                },
                {
                    "id": "skill:upload-kept",
                    "lane": "hive_skills",
                    "source_type": "skill_descriptor",
                    "source_id": "upload-kept",
                    "title": "Podcast SEO review helper",
                    "metadata": {
                        "skill_id": "upload-kept",
                        "name": "Podcast SEO review helper",
                        "object_key": "skills/S999_podcast-seo-review-helper.json",
                        "source_lane": "hive_skills",
                        "source_object_key": "skills/S999_podcast-seo-review-helper.json",
                        "hive_lane": "podcast-seo",
                        "tags": ["uploaded-file", "podcast"],
                        "created_from_hive_skills_folder_file": True,
                    },
                },
            ],
        }

    def delete_metadata_ids(self, item_ids: list[str]) -> dict[str, object]:
        self.deleted.extend(item_ids)
        return {"ok": True, "deleted_count": len(item_ids), "deleted_ids": item_ids, "failed": []}


def test_cleanup_uploaded_file_skills_dry_run_identifies_only_legacy_file_records(monkeypatch) -> None:
    fake = _FakeD1(Settings(APP_ENV="test"))
    monkeypatch.setattr(registry, "D1MetadataStore", lambda settings: fake)

    result = registry.cleanup_uploaded_file_skill_records(settings=Settings(APP_ENV="test"))

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["candidate_count"] == 1
    assert result["r2_deletes_attempted"] == 0
    assert result["candidates"][0]["id"] == "skill:upload-badpng"
    assert fake.deleted == []


def test_cleanup_uploaded_file_skills_requires_confirmation_before_deleting(monkeypatch) -> None:
    fake = _FakeD1(Settings(APP_ENV="test"))
    monkeypatch.setattr(registry, "D1MetadataStore", lambda settings: fake)

    result = registry.cleanup_uploaded_file_skill_records(
        settings=Settings(APP_ENV="test"),
        dry_run=False,
    )

    assert result["ok"] is False
    assert result["error_code"] == "cleanup_confirmation_required"
    assert fake.deleted == []


def test_cleanup_uploaded_file_skills_live_deletes_only_candidates(monkeypatch) -> None:
    fake = _FakeD1(Settings(APP_ENV="test"))
    monkeypatch.setattr(registry, "D1MetadataStore", lambda settings: fake)

    result = registry.cleanup_uploaded_file_skill_records(
        settings=Settings(APP_ENV="test"),
        dry_run=False,
        confirm="delete-uploaded-file-skills",
    )

    assert result["ok"] is True
    assert result["deleted_count"] == 1
    assert fake.deleted == ["skill:upload-badpng"]
