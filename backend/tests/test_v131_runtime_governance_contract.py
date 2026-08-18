from pathlib import Path

from app.core.config import Settings
from app.core.version import BUILD_STAGE
from app.main import create_app


def test_v131_runtime_version_markers_are_aligned() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_version == "1.31-production"
    assert BUILD_STAGE == "v1.31-production-readiness"
    assert "APP_VERSION=1.31-production" in Path(".env.example").read_text()
    assert "APP_VERSION=1.31-production" in Path("HIVE-PRODUCTION-SHARED.env").read_text()


def test_governance_and_model_optimisation_routes_are_shipped() -> None:
    app = create_app(Settings(_env_file=None))
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
    required = {
        ("/v1/runtime/readiness", "GET"),
        ("/v1/system/repo-health", "GET"),
        ("/v1/providers/health", "GET"),
        ("/v1/environment/audit", "GET"),
        ("/v1/model-registry", "GET"),
        ("/v1/ai-council/run", "POST"),
        ("/v1/optimisation/stats", "GET"),
        ("/v1/monthly-review/generate", "POST"),
    }
    assert required <= routes
