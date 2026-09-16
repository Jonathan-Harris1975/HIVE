from __future__ import annotations

from app.core.config import Settings
from app.services.skill_registry import _score_skill_item, list_skills_catalogue


def test_v19_weighted_skill_search_matches_split_terms() -> None:
    item = {
        "id": "skill:S900",
        "title": "rss-feed-rewriter",
        "source_type": "skill_descriptor",
        "source_id": "S900",
        "url": "repo://skills/catalogue_metadata.json#HIVE-sk900",
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
        "url": "repo://skills/catalogue_metadata.json#HIVE-sk901",
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


def test_v19_local_catalogue_records_have_native_provenance() -> None:
    result = list_skills_catalogue(settings=Settings(APP_ENV="test"), limit=20)

    assert result["ok"] is True
    assert result["source"] == "repo://skills/catalogue_metadata.json"
    assert all(item["source_type"] == "repository_skill" for item in result["items"])
    assert all(item["metadata"]["implementation_paths"] for item in result["items"])
