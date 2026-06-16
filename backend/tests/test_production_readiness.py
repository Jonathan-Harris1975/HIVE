import pytest
from app.core.config import Settings
from app.core.production import (
    ProductionConfigurationError,
    build_readiness_report,
    enforce_production_readiness,
)
from app.main import create_app
from fastapi.testclient import TestClient


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "production",
        "APP_VERSION": "test-production",
        "ADMIN_BEARER_TOKEN": "a" * 48,
        "CORS_ORIGINS": "https://hive-ui.pages.dev",
        "ALLOWED_HOSTS": "testserver,*.koyeb.app",
        "PRODUCTION_REQUIRE_OPENROUTER": False,
        "PRODUCTION_REQUIRE_R2": False,
        "PRODUCTION_REQUIRE_DATABASE": False,
        "MAX_UPLOAD_BYTES": 1024,
        "MAX_REQUEST_BODY_BYTES": 2048,
    }
    values.update(overrides)
    return Settings(**values)


def test_production_readiness_accepts_minimal_hardened_configuration() -> None:
    report = build_readiness_report(_production_settings())

    assert report.ready is True
    assert report.errors == ()
    assert report.public_payload()["app_version"] == "test-production"


def test_production_readiness_rejects_default_admin_token() -> None:
    settings = _production_settings(ADMIN_BEARER_TOKEN="change-me-local-only")

    with pytest.raises(ProductionConfigurationError, match="ADMIN_BEARER_TOKEN"):
        enforce_production_readiness(settings)


def test_production_readiness_rejects_wildcard_cors() -> None:
    settings = _production_settings(CORS_ORIGINS="*")

    report = build_readiness_report(settings)
    assert report.ready is False
    assert any(item.name == "cors_origins" for item in report.errors)


def test_production_docs_are_disabled_and_runtime_is_hardened() -> None:
    app = create_app(_production_settings())

    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None

    with TestClient(app) as client:
        response = client.get("/livez", headers={"X-Request-ID": "test-request-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "test-request-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["strict-transport-security"].startswith("max-age=")
    assert response.headers["cache-control"] == "no-store"


def test_request_body_limit_returns_413_before_route_processing() -> None:
    settings = Settings(
        APP_ENV="test",
        MAX_UPLOAD_BYTES=8,
        MAX_REQUEST_BODY_BYTES=16,
        REQUEST_LOGGING_ENABLED=False,
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.post(
            "/v1/files/upload-text",
            json={"filename": "large.txt", "content": "x" * 100},
        )

    assert response.status_code == 413
    payload = response.json()
    assert payload["detail"] == "Request body exceeds the configured limit"
    assert payload["max_bytes"] == 16
    assert response.headers["x-request-id"] == payload["request_id"]


def test_detailed_readiness_requires_auth_in_production() -> None:
    settings = _production_settings()
    app = create_app(settings)

    with TestClient(app) as client:
        missing = client.get("/v1/runtime/readiness")
        accepted = client.get(
            "/v1/runtime/readiness",
            headers={"Authorization": f"Bearer {settings.admin_bearer_token}"},
        )

    assert missing.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["ready"] is True


def test_comma_separated_security_lists_parse_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://one.example,https://two.example")
    monkeypatch.setenv("ALLOWED_HOSTS", "api.example,*.koyeb.app")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["https://one.example", "https://two.example"]
    assert settings.allowed_hosts == ["api.example", "*.koyeb.app"]


def test_unhandled_errors_return_safe_request_id_response() -> None:
    app = create_app(_production_settings())

    @app.get("/__test-error")
    async def explode() -> None:
        raise RuntimeError("sensitive internal detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test-error", headers={"X-Request-ID": "error-case-1"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error", "request_id": "error-case-1"}
    assert response.headers["x-request-id"] == "error-case-1"
    assert "sensitive internal detail" not in response.text
