from __future__ import annotations

from app.services.skill_registry import _score_skill_item, _skill_document_to_metadata


def test_v19_weighted_skill_search_matches_split_terms() -> None:
    item = {
        "id": "skill:S900",
        "title": "rss-feed-rewriter",
        "source_type": "skill_descriptor",
        "source_id": "S900",
        "url": "https://example.test/skills/S900.json",
        "metadata": {
            "slug": "rss-feed-rewriter",
            "tags": ["rss", "content", "rewrite", "repo-aims"],
            "hive_lane": "Website/content",
            "catalogue_category": "content-operations",
            "repos": ["HIVE", "AIMS"],
            "priority_tier": "P1 - High",
            "risk_level": "low",
            "indexable_text": "Rewrite RSS feed summaries and keep content aligned for AIMS.",
        },
    }

    scored = _score_skill_item(item, "RSS rewrite")

    assert scored["score"] > 0
    assert "rss" in scored["matched_terms"]
    assert "rewrite" in scored["matched_terms"]
    assert "title" in scored["matched_fields"] or "tags" in scored["matched_fields"]
    assert "P1 - High" in scored["score_explanation"]


def test_v19_weighted_skill_search_uses_synonyms() -> None:
    item = {
        "id": "skill:S901",
        "title": "feed-summary-copy",
        "source_type": "skill_descriptor",
        "source_id": "S901",
        "url": "https://example.test/skills/S901.json",
        "metadata": {
            "slug": "feed-summary-copy",
            "tags": ["syndication", "content"],
            "hive_lane": "Website/content",
            "catalogue_category": "content-operations",
            "repos": ["HIVE"],
            "priority_tier": "P2 - Useful",
            "risk_level": "low",
            "indexable_text": "Adjust feed summaries and content copy.",
        },
    }

    scored = _score_skill_item(item, "RSS rewrite")

    assert scored["score"] > 0
    assert set(scored["matched_terms"]) == {"rss", "rewrite"}


def test_v19_skill_document_mapping_still_preserves_descriptor_url(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("R2_BUCKET_HIVE_SKILLS", "hive-skills")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL_HIVE_SKILLS", "https://skills.example.test")
    get_settings.cache_clear()
    settings = get_settings()

    mapped = _skill_document_to_metadata(settings, {
        "document_id": "skill:S902",
        "reference_prefix": "S902",
        "name": "podcast-seo",
        "object_key": "skills/S902_podcast-seo.json",
        "text": "Podcast SEO and metadata skill.",
        "metadata": {
            "skill_id": "S902",
            "reference_prefix": "S902",
            "slug": "podcast-seo",
            "priority_tier": "P0 - Foundation",
            "hive_lane": "SEO/AEO/GEO",
            "risk_level": "low",
            "repos": ["HIVE", "AIMS"],
        },
        "tags": ["podcast", "seo"],
    })

    assert mapped["id"] == "skill:S902"
    assert mapped["metadata"]["descriptor_url"] == "https://skills.example.test/skills/S902_podcast-seo.json"
