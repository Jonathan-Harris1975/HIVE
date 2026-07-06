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


def _check(
    name: str, ok: bool, success: str, failure: str, *, required: bool = False
) -> ReadinessCheck:
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

    ops_token = settings.ops_event_ingest_token.strip()
    ops_token_valid = bool(ops_token) and len(ops_token) >= 32
    checks.append(
        _check(
            "ops_event_ingest",
            not settings.ops_event_ingest_enabled or ops_token_valid,
            "Operational event ingestion is disabled or protected by a dedicated token.",
            "OPS_EVENT_INGEST_ENABLED=true requires OPS_EVENT_INGEST_TOKEN of at least 32 characters.",
            required=production and settings.ops_event_ingest_enabled,
        )
    )

    rams_qa_token = settings.rams_qa_ingest_token.strip()
    rams_qa_token_valid = bool(rams_qa_token) and len(rams_qa_token) >= 32
    checks.append(
        _check(
            "rams_qa_ingest",
            not settings.rams_qa_ingest_enabled or rams_qa_token_valid,
            "RAMS QA-event ingestion is disabled or protected by a dedicated token.",
            "RAMS_QA_INGEST_ENABLED=true requires RAMS_QA_INGEST_TOKEN of at least 32 characters.",
            required=production and settings.rams_qa_ingest_enabled,
        )
    )

    mast_mode = settings.mast_monitor_mode.strip().lower()
    mast_monitor_ready = (
        mast_mode == "disabled"
        or (mast_mode == "http" and bool(settings.mast_health_url.strip()))
        or (
            mast_mode == "r2"
            and bool(settings.r2_bucket_meta_system.strip())
            and bool(settings.mast_state_object_key.strip())
            and settings.r2_read_credentials_configured
        )
    )
    checks.append(
        _check(
            "mast_monitoring",
            mast_monitor_ready,
            "MAST monitoring contract is configured.",
            "MAST_MONITOR_MODE must be disabled, a configured HTTP probe, or an R2 heartbeat with scoped read credentials.",
            required=production and settings.repo_health_enabled,
        )
    )


    rams_readiness_requires_token = bool(
        settings.repo_health_enabled
        and settings.rams_readiness_url.strip()
        and not settings.rams_readiness_bearer_token.strip()
        and not settings.rams_health_bearer_token.strip()
    )
    checks.append(
        _check(
            "rams_readiness_auth",
            not rams_readiness_requires_token,
            "RAMS readiness auth is configured or not required.",
            "RAMS_READINESS_URL is configured but no RAMS_READINESS_BEARER_TOKEN/RAMS_HEALTH_BEARER_TOKEN/RMS_API_KEY is available.",
            required=production and settings.repo_health_enabled and bool(settings.rams_readiness_url.strip()),
        )
    )


    execution_gate_ready = (
        not settings.execution_adapters_enabled
        or bool(settings.execution_adapters_require_approval)
    )
    checks.append(
        _check(
            "execution_adapters",
            execution_gate_ready,
            "Execution adapters are enabled behind the human approval gate or disabled by configuration.",
            "EXECUTION_ADAPTERS_ENABLED=true must keep EXECUTION_ADAPTERS_REQUIRE_APPROVAL=true for this controlled production service.",
            required=production and settings.execution_adapters_enabled,
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

    configured_lanes = {str(item["lane"]): item for item in settings.r2_ecosystem_lanes}
    required_lane_names = settings.required_r2_read_lane_names
    non_primary_buckets_configured = any(
        bool(item.get("bucket")) and not bool(item.get("primary_upload_lane"))
        for item in configured_lanes.values()
    )
    multi_bucket_read_required = bool(
        settings.r2_multi_bucket_read_enabled
        and (settings.production_require_r2 or non_primary_buckets_configured or required_lane_names)
    )
    multi_bucket_read_complete = bool(
        settings.r2_write_credentials_configured
        or (settings.r2_read_access_key_id and settings.r2_read_secret_access_key)
    )
    checks.append(
        _check(
            "r2_multi_bucket_read",
            not multi_bucket_read_required or multi_bucket_read_complete,
            "Scoped multi-bucket R2 read access is configured.",
            "R2 multi-bucket read access is enabled but neither shared write credentials nor read-only credentials are complete.",
            required=production and multi_bucket_read_required,
        )
    )

    unknown_required_lanes = [name for name in required_lane_names if name not in configured_lanes]
    unreadable_required_lanes = [
        name
        for name in required_lane_names
        if name in configured_lanes and not bool(configured_lanes[name].get("readable"))
    ]
    lane_contract_ok = not unknown_required_lanes and not unreadable_required_lanes
    lane_failure_parts: list[str] = []
    if unknown_required_lanes:
        lane_failure_parts.append(f"unknown lanes: {', '.join(unknown_required_lanes)}")
    if unreadable_required_lanes:
        lane_failure_parts.append(f"unreadable lanes: {', '.join(unreadable_required_lanes)}")
    checks.append(
        _check(
            "r2_required_lanes",
            lane_contract_ok,
            f"All {len(required_lane_names)} required R2 lanes have server-side read access.",
            "Required R2 lane contract is incomplete (" + "; ".join(lane_failure_parts) + ").",
            required=production and settings.production_require_r2 and bool(required_lane_names),
        )
    )

    skills_lane = configured_lanes.get("hive_skills") or {}
    skills_contract_ok = bool(
        skills_lane.get("bucket")
        and skills_lane.get("public_base_url")
        and (skills_lane.get("readable") or settings.skill_registry_fallback_enabled)
    )
    checks.append(
        _check(
            "shared_skills_source",
            skills_contract_ok or not (production and settings.production_require_r2),
            "The shared skills bucket, public source and retrieval path are configured.",
            "Production requires the HIVE shared skills bucket and public base URL, with either scoped R2 reads or the bounded public fallback enabled.",
            required=production and settings.production_require_r2,
        )
    )

    database_ready = settings.database_enabled and bool(settings.sql_database_url)
    database_required = production and settings.production_require_database
    checks.append(
        _check(
            "database",
            database_ready
            or (not settings.database_enabled and not settings.production_require_database),
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
        settings.vectorize_account_id
        and settings.vectorize_api_token
        and settings.vectorize_index_name
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
        settings.embeddings_account_id
        and settings.embeddings_api_token
        and settings.embeddings_model
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
        production and (parsed.scheme != "https" or host in {"localhost", "127.0.0.1", "::1"})
    )
