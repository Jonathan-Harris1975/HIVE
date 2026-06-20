import pytest
from fastapi import HTTPException

from app.api.skills import SkillFromFileRequest, skill_from_file
from app.core.config import Settings


def test_skill_from_file_dry_run_requires_hive_skills_folder() -> None:
    settings = Settings(
        APP_ENV="test",
        R2_BUCKET_UPLOADS="uploads",
        R2_PUBLIC_BASE_URL_UPLOADS="https://uploads.example.invalid",
        R2_BUCKET_HIVE_SKILLS="hive-skills",
        R2_PUBLIC_BASE_URL_HIVE_SKILLS="https://skills.example.invalid",
    )

    with pytest.raises(HTTPException) as raised:
        skill_from_file(
            SkillFromFileRequest(
                title="Brand fallback hero asset",
                object_key="uploads/2026/06/blog-fallback-hero.png",
                source_lane="uploads",
                description="Use this asset when auditing brand fallback hero images.",
                repo="HIVE-UI",
                hive_lane="brand-assets",
                priority_tier="P2",
                risk_level="low",
                tags=["brand-assets", "image"],
                dry_run=True,
            ),
            settings=settings,
        )

    assert raised.value.status_code == 400
    assert raised.value.detail["error_code"] == "skill_source_not_hive_skills_folder"


def test_skill_from_file_dry_run_uses_reviewed_hive_skills_metadata() -> None:
    settings = Settings(
        APP_ENV="test",
        R2_BUCKET_HIVE_SKILLS="hive-skills",
        R2_PUBLIC_BASE_URL_HIVE_SKILLS="https://skills.example.invalid",
    )
    result = skill_from_file(
        SkillFromFileRequest(
            title="Podcast SEO review helper",
            object_key="skills/S999_podcast-seo-review-helper.json",
            source_lane="hive_skills",
            description="Review podcast pages against SEO and freshness rules.",
            repo="HIVE",
            hive_lane="podcast-seo",
            priority_tier="P2",
            risk_level="low",
            tags=["podcast", "seo"],
            dry_run=True,
        ),
        settings=settings,
    )

    assert result["ok"] is True
    skill = result["skill"]
    metadata = skill["metadata"]
    assert skill["title"] == "Podcast SEO review helper"
    assert metadata["description"] == "Review podcast pages against SEO and freshness rules."
    assert metadata["source_lane"] == "hive_skills"
    assert metadata["source_object_key"] == "skills/S999_podcast-seo-review-helper.json"
    assert metadata["hive_lane"] == "podcast-seo"
    assert metadata["repos"] == ["HIVE"]
    assert metadata["created_from_hive_skills_folder_file"] is True
    assert "uploaded-file" in metadata["tags"]
