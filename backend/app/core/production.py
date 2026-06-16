from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from app.core.config import Settings


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    message: str
    required: bool = False


@dataclass(frozen=True)
class ReadinessReport:
    ready: bool
    environment: str
    app_version: str
    checks: tuple[ReadinessCheck, ...]

    @property
    def errors(self) -> tuple[ReadinessCheck, ...]:
        return tuple(item for item in self.checks if item.status == "error")

    @property
    def warnings(self) -> tuple[ReadinessCheck, ...]:
        return tuple(item for item in self.checks if item.status == "warning")

    def public_payload(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "environment": self.environment,
            "app_version": self.app_version,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
        }

    def detailed_payload(self) -> dict[str, object]:
        payload = self.public_payload()
        payload["checks"] = [asdict(item) for item in self.checks]
        return payload


class ProductionConfigurationError(RuntimeError):
    pass


def _check(name: str, ok: bool, success: str, failure: str, *, required: bool = False) -> ReadinessCheck:
    if ok:
        return ReadinessCheck(name=name, status="ok", message=success, required=required)
    return ReadinessCheck(
        name=name,
        status="error" if required else "warning",
        message=failure,
        required=required,
    )


def build_readiness_report(settings: Settings) -> ReadinessReport:
    production = not settings.is_dev
    checks: list[ReadinessCheck] = []

    token = settings.admin_bearer_token.strip()
    token_valid = token not in {"", "change-me-local-only"} and len(token) >= 32
    checks.append(
        _check(
            "admin_bearer_token",
            token_valid or not production,
            "Admin bearer token is configured.",
            "Production requires a unique ADMIN_BEARER_TOKEN of at least 32 characters.",
            required=production,
        )
    )

    origins = [item.strip() for item in settings.cors_origins if item.strip()]
    invalid_origins = [item for item in origins if not _valid_origin(item, production=production)]
    checks.append(
        _check(
            "cors_origins",
            not invalid_origins,
            "CORS origins are explicit and valid.",
            "CORS_ORIGINS contains wildcard, localhost, or a non-HTTPS production origin.",
            required=production,
        )
    )

    allowed_hosts = settings.effective_allowed_hosts
    wildcard_host = "*" in allowed_hosts
    checks.append(
        _check(
            "allowed_hosts",
            not (production and settings.trusted_hosts_enabled and wildcard_host),
            "Trusted hosts are restricted.",
            "ALLOWED_HOSTS still permits every host. Set the Koyeb hostname and any custom API domain.",
            required=False,
        )
    )

    checks.append(
        _check(
            "openrouter",
            bool(settings.openrouter_api_key) or not settings.production_require_openrouter,
            "OpenRouter is configured.",
            "OPENROUTER_API_KEY is missing while PRODUCTION_REQUIRE_OPENROUTER=true.",
            required=production and settings.production_require_openrouter,
        )
    )

    r2_values = (
        settings.r2_endpoint_url,
        settings.cf_r2_access_key_id,
        settings.cf_r2_secret_access_key,
        settings.cf_r2_bucket,
    )
    r2_any = any(r2_values[:3])
    r2_complete = all(r2_values)
    r2_required = production and settings.production_require_r2
    checks.append(
        _check(
            "r2",
            r2_complete or (not r2_any and not settings.production_require_r2),
            "Primary R2 upload storage is configured.",
            "R2 configuration is missing or partial. Check account ID, access key, secret, and bucket.",
            required=r2_required or (production and r2_any),
        )
    )

    multi_bucket_read_complete = bool(
        settings.r2_read_access_key_id and settings.r2_read_secret_access_key
    )
    checks.append(
        _check(
            "r2_multi_bucket_read",
            not settings.r2_multi_bucket_read_enabled or multi_bucket_read_complete,
            "Scoped multi-bucket R2 read access is configured.",
            "R2_MULTI_BUCKET_READ_ENABLED=true but the read-only access key or secret is missing.",
            required=production and settings.r2_multi_bucket_read_enabled,
        )
    )

    database_ready = settings.database_enabled and bool(settings.sql_database_url)
    database_required = production and settings.production_require_database
    checks.append(
        _check(
            "database",
            database_ready or (not settings.database_enabled and not settings.production_require_database),
            "SQL persistence is configured.",
            "SQL persistence is enabled or required, but DATABASE_URL/fields are incomplete.",
            required=database_required or (production and settings.database_enabled),
        )
    )

    d1_complete = bool(settings.d1_account_id and settings.d1_api_key and settings.d1_database_id)
    checks.append(
        _check(
            "d1",
            not settings.d1_enabled or d1_complete,
            "D1 configuration is coherent.",
            "D1_ENABLED=true but account, token, or database ID is missing.",
            required=production and settings.d1_enabled,
        )
    )

    vectorize_complete = bool(
        settings.vectorize_account_id and settings.vectorize_api_token and settings.vectorize_index_name
    )
    checks.append(
        _check(
            "vectorize",
            not settings.vectorize_enabled or vectorize_complete,
            "Vectorize configuration is coherent.",
            "VECTORIZE_ENABLED=true but account, token, or index name is missing.",
            required=production and settings.vectorize_enabled,
        )
    )

    embeddings_complete = bool(
        settings.embeddings_account_id and settings.embeddings_api_token and settings.embeddings_model
    )
    checks.append(
        _check(
            "embeddings",
            not settings.embeddings_enabled or embeddings_complete,
            "Embeddings configuration is coherent.",
            "EMBEDDINGS_ENABLED=true but account, token, or model is missing.",
            required=production and settings.embeddings_enabled,
        )
    )

    checks.append(
        _check(
            "request_body_limit",
            settings.max_request_body_bytes >= settings.max_upload_bytes,
            "Request body limit accommodates the configured upload limit.",
            "MAX_REQUEST_BODY_BYTES must be greater than or equal to MAX_UPLOAD_BYTES.",
            required=production,
        )
    )

    errors = tuple(item for item in checks if item.status == "error")
    return ReadinessReport(
        ready=not errors,
        environment=settings.app_env,
        app_version=settings.app_version,
        checks=tuple(checks),
    )


def enforce_production_readiness(settings: Settings) -> ReadinessReport:
    report = build_readiness_report(settings)
    if not settings.is_dev and not report.ready:
        summary = "; ".join(f"{item.name}: {item.message}" for item in report.errors)
        raise ProductionConfigurationError(f"HIVE production configuration is invalid: {summary}")
    return report


def _valid_origin(origin: str, *, production: bool) -> bool:
    if origin == "*":
        return False
    parsed = urlparse(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = (parsed.hostname or "").lower()
    return not (
        production
        and (parsed.scheme != "https" or host in {"localhost", "127.0.0.1", "::1"})
    )
