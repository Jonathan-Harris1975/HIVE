from __future__ import annotations

from app.services import skill_registry as registry


class _SettingsStub:
    def public_url_for_r2_lane(self, lane: str, key: str) -> str:
        base = "https://skills.example.test"
        return f"{base}/{key}" if key else base


SETTINGS = _SettingsStub()


def _sample_items():
    return [
        {
            "id": "skill:S194",
            "lane": "hive_skills",
            "source_type": "skill_descriptor",
            "source_id": "S194",
            "title": "podcast-seo",
            "url": "https://skills.example.test/skills/S194_podcast-seo.json",
            "metadata": {
                "skill_id": "S194",
                "reference_prefix": "S194",
                "slug": "podcast-seo",
                "name": "podcast-seo",
                "priority_tier": "P0 - Foundation",
                "hive_lane": "SEO/AEO/GEO",
                "risk_level": "low",
                "repos": ["HIVE", "AIMS", "RAMS", "Website"],
                "tags": ["podcast-seo", "seo-aeo-geo", "repo-aims", "risk-low"],
                "catalogue_category": "content-operations",
                "descriptor_url": "https://skills.example.test/skills/S194_podcast-seo.json",
                "indexable_text": "Podcast SEO fresh signal review for AIMS amplification and RSS wording.",
            },
        },
        {
            "id": "skill:S198",
            "lane": "hive_skills",
            "source_type": "skill_descriptor",
            "source_id": "S198",
            "title": "sentry-cli",
            "url": "https://skills.example.test/skills/S198_sentry-cli.json",
            "metadata": {
                "skill_id": "S198",
                "reference_prefix": "S198",
                "slug": "sentry-cli",
                "name": "sentry-cli",
                "priority_tier": "P1 - High",
                "hive_lane": "Ops/Monitoring",
                "risk_level": "medium",
                "repos": ["HIVE", "AIMS", "RAMS", "Website"],
                "tags": ["ops-monitoring", "sentry-cli", "risk-medium"],
                "catalogue_category": "risk-and-audit",
                "descriptor_url": "https://skills.example.test/skills/S198_sentry-cli.json",
                "indexable_text": "Monitoring and error diagnostics for production services.",
            },
        },
    ]


def test_v19_weighted_skill_search_scores_relevant_fields(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_skill_records",
        lambda **kwargs: {"ok": True, "items": _sample_items()},
    )

    result = registry.search_skills_catalogue(settings=SETTINGS, query="podcast seo", limit=5)

    assert result["ok"] is True
    assert result["items"][0]["title"] == "podcast-seo"
    assert result["items"][0]["score"] > 0
    assert "title" in result["items"][0]["matched_fields"] or "tags" in result["items"][0]["matched_fields"]


def test_v110_recommendation_engine_respects_repo_and_risk(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_skill_records",
        lambda **kwargs: {"ok": True, "items": _sample_items()},
    )

    result = registry.recommend_skills(
        settings=SETTINGS,
        task="podcast SEO fresh signal review",
        repo="AIMS",
        risk_ceiling="low",
        limit=5,
    )

    assert result["ok"] is True
    assert result["recommendations"][0]["skill_id"] == "S194"
    assert result["recommendations"][0]["execution_policy"]["auto_execute_allowed"] is False


def test_v111_route_plan_is_review_gated(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_skill_records",
        lambda **kwargs: {"ok": True, "items": _sample_items()},
    )

    result = registry.route_skill_request(
        settings=SETTINGS,
        task="triage production monitoring issue",
        repo="AIMS",
        limit=2,
    )

    assert result["ok"] is True
    assert result["execution_policy"] == "review_gated"
    assert result["route_plan"][-1]["name"] == "approval_gate"


def test_v112_shared_execution_plan_is_review_gated(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_skill_records",
        lambda **kwargs: {"ok": True, "items": _sample_items()},
    )

    result = registry.shared_execution_plan(
        settings=SETTINGS,
        task="review podcast SEO workflow",
        repo="AIMS",
        workflow_preset="podcast_episode_review",
        limit=2,
    )

    assert result["ok"] is True
    assert result["build_stage_hint"] == "v1.26.11-env-split"
    assert result["execution_mode"] == "review_gated_execution"
    assert result["can_execute_now"] is False
    assert result["guardrails"]["no_auto_install"] is True
