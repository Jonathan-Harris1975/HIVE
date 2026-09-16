from __future__ import annotations

from app.services import skill_registry as registry


class _SettingsStub:
    pass


SETTINGS = _SettingsStub()


def _sample_items():
    return [
        {
            "id": "skill:HIVE-sk003",
            "lane": "hive_local_skills",
            "source_type": "repository_skill",
            "source_id": "HIVE-sk003",
            "title": "audit-report-review",
            "url": "repo://skills/catalogue_metadata.json#HIVE-sk003",
            "metadata": {
                "skill_id": "HIVE-sk003",
                "reference_prefix": "HIVE-sk003",
                "slug": "audit-report-review",
                "name": "audit-report-review",
                "priority_tier": "P0 - Foundation",
                "hive_lane": "SEO/AEO/GEO",
                "risk_level": "low",
                "repos": ["HIVE", "AIMS", "RAMS", "Website"],
                "tags": ["audit", "quality", "repo-aims", "risk-low"],
                "catalogue_category": "audit-and-readiness",
                "source_uri": "repo://skills/catalogue_metadata.json#HIVE-sk003",
                "indexable_text": "Audit evidence review for AIMS quality control and readiness.",
            },
        },
        {
            "id": "skill:HIVE-sk001",
            "lane": "hive_local_skills",
            "source_type": "repository_skill",
            "source_id": "HIVE-sk001",
            "title": "ci-log-analysis",
            "url": "repo://skills/catalogue_metadata.json#HIVE-sk001",
            "metadata": {
                "skill_id": "HIVE-sk001",
                "reference_prefix": "HIVE-sk001",
                "slug": "ci-log-analysis",
                "name": "ci-log-analysis",
                "priority_tier": "P1 - High",
                "hive_lane": "Ops/Monitoring",
                "risk_level": "medium",
                "repos": ["HIVE", "AIMS", "RAMS", "Website"],
                "tags": ["ops-monitoring", "logs", "risk-medium"],
                "catalogue_category": "risk-and-audit",
                "source_uri": "repo://skills/catalogue_metadata.json#HIVE-sk001",
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

    result = registry.search_skills_catalogue(settings=SETTINGS, query="audit quality", limit=5)

    assert result["ok"] is True
    assert result["items"][0]["title"] == "audit-report-review"
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
        task="audit quality evidence review",
        repo="AIMS",
        risk_ceiling="low",
        limit=5,
    )

    assert result["ok"] is True
    assert result["recommendations"][0]["skill_id"] == "HIVE-sk003"
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
        task="review audit evidence workflow",
        repo="AIMS",
        workflow_preset="audit_report_review",
        limit=2,
    )

    assert result["ok"] is True
    assert result["build_stage_hint"] == "v1.31-production-readiness"
    assert result["execution_mode"] == "review_gated_execution"
    assert result["can_execute_now"] is False
    assert result["guardrails"]["no_external_skill_install"] is True
