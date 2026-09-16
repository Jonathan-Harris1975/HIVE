from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api import chat as chat_api
from app.core.config import Settings
from app.core.production import build_readiness_report
from app.services import dependency_readiness, skill_registry


def _skills_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"APP_ENV": "test"}
    values.update(overrides)
    return Settings(**values)


def test_skill_catalogue_never_requires_remote_source() -> None:
    status = skill_registry.skills_registry_status(_skills_settings())

    assert status["ok"] is True
    assert status["access_mode"] == "repository-local-read-only"
    assert status["shared_bucket_required"] is False
    assert status["network_fetch_enabled"] is False
    assert status["runtime_install_enabled"] is False


def test_skill_search_reads_bundled_catalogue() -> None:
    result = skill_registry.search_skills_catalogue(
        settings=_skills_settings(),
        query="production readiness configuration",
        repo="HIVE",
    )

    assert result["ok"] is True
    assert result["source"] == "repo://skills/catalogue_metadata.json"
    assert result["items"][0]["metadata"]["implementation_paths"]


def test_build_skill_context_uses_local_capability_summaries() -> None:
    settings = _skills_settings(
        SKILL_CONTEXT_ENABLED=True,
        SKILL_CONTEXT_MAX_ITEMS=2,
        SKILL_CONTEXT_MAX_CHARS=2000,
        SKILL_CONTEXT_RISK_CEILING="medium",
    )

    result = skill_registry.build_skill_context(
        settings=settings,
        task="Audit HIVE model routing",
        repo="HIVE",
    )

    assert result["ok"] is True
    assert result["enabled"] is True
    assert result["skills"]
    assert "[Local capability: HIVE-sk" in result["prompt"]
    assert "do not install, download or execute" in result["prompt"]


def test_chat_payload_injects_bounded_local_skill_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(APP_ENV="test", OPENROUTER_API_KEY="test")
    monkeypatch.setattr(
        chat_api,
        "build_skill_context",
        lambda **kwargs: {
            "ok": True,
            "enabled": True,
            "source": "repo://skills/catalogue_metadata.json",
            "prompt": (
                "Repository-local HIVE capability summary.\n"
                "[Local capability: HIVE-sk006] Production readiness"
            ),
            "skills": [
                {
                    "skill_id": "HIVE-sk006",
                    "title": "Production readiness",
                    "source_uri": "repo://skills/catalogue_metadata.json#HIVE-sk006",
                }
            ],
        },
    )

    payload, _fallbacks, context = chat_api.build_payload_with_context(
        chat_api.ChatRequest(
            message="Audit HIVE production readiness",
            model="test/model",
            use_skills=True,
        ),
        settings,
    )

    system_messages = [
        message["content"] for message in payload["messages"] if message["role"] == "system"
    ]
    assert any("[Local capability: HIVE-sk006]" in content for content in system_messages)
    assert context["skills"][0]["skill_id"] == "HIVE-sk006"


def test_removed_skill_lane_is_not_a_storage_alias() -> None:
    settings = _skills_settings(CF_R2_BUCKET="uploads")

    assert settings.r2_lane("skills") is None
    assert settings.internal_r2_lane("hive_skills") is None


def test_production_readiness_accepts_required_operational_r2_lanes() -> None:
    settings = Settings(
        APP_ENV="production",
        ADMIN_BEARER_TOKEN="x" * 48,
        CORS_ORIGINS="https://hive.jonathan-harris.online",
        ALLOWED_HOSTS="hive-api.example",
        PRODUCTION_REQUIRE_OPENROUTER=False,
        PRODUCTION_REQUIRE_R2=True,
        REPO_HEALTH_ENABLED=False,
        CF_R2_ACCOUNT_ID="account",
        CF_R2_ACCESS_KEY_ID="write-key",
        CF_R2_SECRET_ACCESS_KEY="write-secret",
        CF_R2_BUCKET="uploads",
        R2_REQUIRED_READ_LANES="uploads",
    )

    report = build_readiness_report(settings)

    assert report.ready is True
    required_lanes = next(item for item in report.checks if item.name == "r2_required_lanes")
    assert required_lanes.status == "ok"
    local_skills = next(item for item in report.checks if item.name == "local_skills_catalogue")
    assert local_skills.status == "ok"


def test_dependency_readiness_probes_only_required_storage_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        APP_ENV="production",
        ADMIN_BEARER_TOKEN="x" * 48,
        CORS_ORIGINS="https://hive.jonathan-harris.online",
        ALLOWED_HOSTS="hive-api.example",
        PRODUCTION_REQUIRE_OPENROUTER=False,
        PRODUCTION_REQUIRE_R2=True,
        REPO_HEALTH_ENABLED=False,
        CF_R2_ACCOUNT_ID="account",
        CF_R2_ACCESS_KEY_ID="write-key",
        CF_R2_SECRET_ACCESS_KEY="write-secret",
        CF_R2_BUCKET="uploads",
        R2_REQUIRED_READ_LANES="uploads",
    )
    calls: list[tuple[str, str]] = []

    class FakeR2Storage:
        def __init__(self, _settings: Settings) -> None:
            pass

        def list_objects_page(self, **kwargs: object) -> SimpleNamespace:
            calls.append(("list", str(kwargs["bucket"])))
            return SimpleNamespace(objects=[])

    monkeypatch.setattr(dependency_readiness, "R2Storage", FakeR2Storage)
    dependency_readiness.clear_dependency_readiness_cache()

    report = dependency_readiness.build_dependency_readiness_report(settings, force=True)

    assert report.ready is True
    assert calls == [("list", "uploads")]
