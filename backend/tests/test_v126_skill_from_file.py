from app.api.skills import SkillFromFileRequest, skill_from_file
from app.core.config import Settings


def test_skill_from_file_dry_run_uses_reviewed_metadata() -> None:
    settings = Settings(
        APP_ENV="test",
        R2_BUCKET_UPLOADS="uploads",
        R2_PUBLIC_BASE_URL_UPLOADS="https://uploads.example.invalid",
    )
    result = skill_from_file(
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

    assert result["ok"] is True
    skill = result["skill"]
    metadata = skill["metadata"]
    assert skill["title"] == "Brand fallback hero asset"
    assert metadata["description"] == "Use this asset when auditing brand fallback hero images."
    assert metadata["source_lane"] == "uploads"
    assert metadata["source_object_key"] == "uploads/2026/06/blog-fallback-hero.png"
    assert metadata["hive_lane"] == "brand-assets"
    assert metadata["repos"] == ["HIVE-UI"]
    assert "uploaded-file" in metadata["tags"]
