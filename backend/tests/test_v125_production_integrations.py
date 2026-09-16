from __future__ import annotations

from types import SimpleNamespace

from app.core.config import Settings
from app.core.production import build_readiness_report
from app.services import dependency_readiness


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


def test_dependency_readiness_probes_only_required_storage_lanes(
    monkeypatch,
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
