from app.core.config import Settings
from app.main import create_app


def test_retired_model_council_cannot_be_invoked_but_provider_health_and_reports_remain():
    paths = create_app(Settings(_env_file=None)).openapi()["paths"]
    assert not any(path.startswith("/v1/ai-council") for path in paths)
    assert "/v1/providers/health" in paths
    assert "/v1/monthly-review/generate" in paths
    assert "/v1/benchmark/rank" in paths
