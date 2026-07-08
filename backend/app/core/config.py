from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import quote, unquote

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = Field(
        "JH Ops Chat", validation_alias=AliasChoices("APP_NAME", "OPENROUTER_APP_NAME")
    )
    app_env: str = Field("development", validation_alias=AliasChoices("APP_ENV"))
    app_version: str = Field("1.30-production", validation_alias=AliasChoices("APP_VERSION"))
    admin_bearer_token: str = Field(
        "change-me-local-only", validation_alias=AliasChoices("ADMIN_BEARER_TOKEN")
    )
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        validation_alias=AliasChoices("CORS_ORIGINS"),
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"], validation_alias=AliasChoices("ALLOWED_HOSTS")
    )
    api_docs_enabled: bool = Field(False, validation_alias=AliasChoices("API_DOCS_ENABLED"))
    security_headers_enabled: bool = Field(
        True, validation_alias=AliasChoices("SECURITY_HEADERS_ENABLED")
    )
    request_logging_enabled: bool = Field(
        True, validation_alias=AliasChoices("REQUEST_LOGGING_ENABLED")
    )
    trusted_hosts_enabled: bool = Field(
        True, validation_alias=AliasChoices("TRUSTED_HOSTS_ENABLED")
    )
    max_request_body_bytes: int = Field(
        110 * 1024 * 1024, validation_alias=AliasChoices("MAX_REQUEST_BODY_BYTES")
    )
    production_require_openrouter: bool = Field(
        True, validation_alias=AliasChoices("PRODUCTION_REQUIRE_OPENROUTER")
    )
    production_require_r2: bool = Field(
        True, validation_alias=AliasChoices("PRODUCTION_REQUIRE_R2")
    )
    production_require_database: bool = Field(
        False, validation_alias=AliasChoices("PRODUCTION_REQUIRE_DATABASE")
    )
    readiness_dependency_probes_enabled: bool = Field(
        True, validation_alias=AliasChoices("READINESS_DEPENDENCY_PROBES_ENABLED")
    )
    readiness_dependency_probe_cache_seconds: int = Field(
        30, validation_alias=AliasChoices("READINESS_DEPENDENCY_PROBE_CACHE_SECONDS")
    )

    # Production execution adapter gate. Approval unlocks allow-listed
    # operator-triggered handoff; decision endpoints never auto-run side effects.
    execution_adapters_enabled: bool = Field(
        True, validation_alias=AliasChoices("EXECUTION_ADAPTERS_ENABLED")
    )
    execution_adapters_require_approval: bool = Field(
        True, validation_alias=AliasChoices("EXECUTION_ADAPTERS_REQUIRE_APPROVAL")
    )

    # Central operational event inbox used by GitHub, provider deployment watchers
    # and runtime services. The ingest token is separate from the HIVE admin token.
    ops_event_ingest_enabled: bool = Field(
        False, validation_alias=AliasChoices("OPS_EVENT_INGEST_ENABLED")
    )
    ops_event_ingest_token: str = Field(
        "", validation_alias=AliasChoices("OPS_EVENT_INGEST_TOKEN")
    )
    ops_event_memory_limit: int = Field(
        200, ge=10, le=2000, validation_alias=AliasChoices("OPS_EVENT_MEMORY_LIMIT")
    )

    # Read-only ecosystem service monitoring for the HIVE-UI Ops dashboard.
    # Targets are operator-controlled environment values; request callers cannot
    # supply arbitrary URLs. Results are redacted, bounded and cached.
    repo_health_enabled: bool = Field(True, validation_alias=AliasChoices("REPO_HEALTH_ENABLED"))
    repo_health_timeout_seconds: float = Field(
        6.0, validation_alias=AliasChoices("REPO_HEALTH_TIMEOUT_SECONDS")
    )
    repo_health_cache_seconds: int = Field(
        30, validation_alias=AliasChoices("REPO_HEALTH_CACHE_SECONDS")
    )
    hive_ui_health_url: str = Field(
        "https://hive.jonathan-harris.online/health",
        validation_alias=AliasChoices("HIVE_UI_HEALTH_URL"),
    )
    aims_health_url: str = Field(
        "https://app.jonathan-harris.online/livez", validation_alias=AliasChoices("AIMS_HEALTH_URL")
    )
    aims_operational_health_url: str = Field(
        "https://app.jonathan-harris.online/ops/health",
        validation_alias=AliasChoices("AIMS_OPERATIONAL_HEALTH_URL"),
    )
    rams_health_url: str = Field(
        "https://mod.jonathan-harris.online/livez", validation_alias=AliasChoices("RAMS_HEALTH_URL")
    )
    rams_readiness_url: str = Field(
        "https://mod.jonathan-harris.online/readiness",
        validation_alias=AliasChoices("RAMS_READINESS_URL"),
    )
    rams_health_bearer_token: str = Field(
        "", validation_alias=AliasChoices("RAMS_HEALTH_BEARER_TOKEN", "RMS_API_KEY")
    )
    rams_readiness_bearer_token: str = Field(
        "",
        validation_alias=AliasChoices(
            "RAMS_READINESS_BEARER_TOKEN", "RAMS_API_KEY", "RMS_API_KEY"
        ),
    )

    # RAMS QA-event ingestion into the Optimisation Engine. This is the
    # missing wire-up the deployment-readiness audit flagged as critical:
    # without it, RAMS QA events never reach the optimisation decision
    # ledger at all. Token is deliberately separate from both the HIVE admin
    # token and the ops-event ingest token, since RAMS is a distinct trusted
    # caller with its own credential.
    rams_qa_ingest_enabled: bool = Field(
        False, validation_alias=AliasChoices("RAMS_QA_INGEST_ENABLED")
    )
    rams_qa_ingest_token: str = Field(
        "", validation_alias=AliasChoices("RAMS_QA_INGEST_TOKEN")
    )

    mast_health_url: str = Field("", validation_alias=AliasChoices("MAST_HEALTH_URL"))
    mast_status_url: str = Field("", validation_alias=AliasChoices("MAST_STATUS_URL"))
    mast_monitor_mode: Literal["auto", "r2", "http", "disabled"] = Field(
        "auto", validation_alias=AliasChoices("MAST_MONITOR_MODE")
    )
    mast_state_r2_lane: str = Field(
        "meta_system", validation_alias=AliasChoices("MAST_STATE_R2_LANE")
    )
    mast_state_object_key: str = Field(
        "state/mast/scheduler-state.json",
        validation_alias=AliasChoices("MAST_STATE_OBJECT_KEY"),
    )
    mast_state_healthy_max_age_seconds: int = Field(
        90,
        ge=20,
        le=3600,
        validation_alias=AliasChoices("MAST_STATE_HEALTHY_MAX_AGE_SECONDS"),
    )
    mast_state_down_max_age_seconds: int = Field(
        300,
        ge=60,
        le=86_400,
        validation_alias=AliasChoices("MAST_STATE_DOWN_MAX_AGE_SECONDS"),
    )
    mast_state_max_bytes: int = Field(
        1_048_576,
        ge=1024,
        le=8_388_608,
        validation_alias=AliasChoices("MAST_STATE_MAX_BYTES"),
    )

    # Used only for the on-demand "wake a standby service" flow: HIVE calls MAST's
    # mutating /services/{service}/resume endpoint (reads of the lifecycle ledger go
    # through the existing durable R2 state above and need no extra config).
    mast_base_url: str = Field("", validation_alias=AliasChoices("MAST_BASE_URL"))
    mast_admin_token: str = Field(
        "", validation_alias=AliasChoices("MAST_ADMIN_TOKEN", "CRON_ADMIN_TOKEN")
    )
    service_wake_timeout_seconds: int = Field(
        150,
        ge=15,
        le=600,
        validation_alias=AliasChoices("SERVICE_WAKE_TIMEOUT_SECONDS"),
    )
    service_wake_poll_interval_seconds: float = Field(
        3.0,
        ge=1.0,
        le=30.0,
        validation_alias=AliasChoices("SERVICE_WAKE_POLL_INTERVAL_SECONDS"),
    )

    irs_health_url: str = Field(
        "https://images.jonathan-harris.online/health.json",
        validation_alias=AliasChoices("IRS_HEALTH_URL"),
    )
    website_health_url: str = Field(
        "https://jonathan-harris.online/health.json",
        validation_alias=AliasChoices("WEBSITE_HEALTH_URL"),
    )

    openrouter_api_key: str = Field("", validation_alias=AliasChoices("OPENROUTER_API_KEY"))
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://jonathan-harris.online"
    openrouter_app_title: str = Field(
        "JH Ops Chat", validation_alias=AliasChoices("OPENROUTER_APP_TITLE", "OPENROUTER_APP_NAME")
    )

    # Model policy is deliberately env-driven. Defaults use currently common
    # OpenRouter aliases / cheap high-context models rather than brittle dated IDs.
    default_model: str = "~google/gemini-flash-latest"
    cheap_model: str = "~google/gemini-flash-latest"
    balanced_model: str = "~google/gemini-flash-latest"
    premium_model: str = "~anthropic/claude-sonnet-latest"
    code_model: str = "x-ai/grok-build-0.1"
    audit_model: str = "~google/gemini-flash-latest"
    openrouter_free_fallback_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    allow_paid_fallback: bool = False
    openrouter_model_preflight_enabled: bool = True
    openrouter_model_list_timeout_seconds: float = 10
    openrouter_attempt_timeout_seconds: float = Field(
        30,
        validation_alias=AliasChoices(
            "OPENROUTER_ATTEMPT_TIMEOUT_SECONDS", "OPENROUTER_REQUEST_TIMEOUT_SECONDS"
        ),
    )
    openrouter_stream_idle_timeout_seconds: float = Field(
        18, validation_alias=AliasChoices("OPENROUTER_STREAM_IDLE_TIMEOUT_SECONDS")
    )
    openrouter_stream_first_token_timeout_seconds: float = Field(
        12, validation_alias=AliasChoices("OPENROUTER_STREAM_FIRST_TOKEN_TIMEOUT_SECONDS")
    )
    openrouter_max_fallback_attempts: int = 1
    openrouter_empty_reply_retry_enabled: bool = True
    openrouter_min_response_tokens: int = 80
    chat_with_file_model_timeout_seconds: float = 30

    cf_r2_account_id: str = Field(
        "", validation_alias=AliasChoices("CF_R2_ACCOUNT_ID", "R2_ACCOUNT_ID")
    )
    cf_r2_access_key_id: str = Field(
        "", validation_alias=AliasChoices("CF_R2_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID")
    )
    cf_r2_secret_access_key: str = Field(
        "", validation_alias=AliasChoices("CF_R2_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY")
    )
    cf_r2_bucket: str = Field(
        "ops-chat-uploads",
        validation_alias=AliasChoices("CF_R2_BUCKET", "R2_BUCKET_UPLOADS", "R2_BUCKET"),
    )
    cf_r2_public_base_url: str = Field(
        "", validation_alias=AliasChoices("CF_R2_PUBLIC_BASE_URL", "R2_PUBLIC_BASE_URL")
    )
    cf_r2_endpoint_url: str = Field(
        "", validation_alias=AliasChoices("CF_R2_ENDPOINT_URL", "R2_ENDPOINT_URL")
    )
    r2_region: str = Field("auto", validation_alias=AliasChoices("R2_REGION", "AWS_REGION"))
    r2_connect_timeout_seconds: int = 8
    r2_read_timeout_seconds: int = 20
    r2_max_attempts: int = 2
    r2_addressing_style: str = "path"
    r2_multi_bucket_read_enabled: bool = Field(
        True, validation_alias=AliasChoices("R2_MULTI_BUCKET_READ_ENABLED")
    )
    r2_multi_bucket_write_enabled: bool = Field(
        True, validation_alias=AliasChoices("R2_MULTI_BUCKET_WRITE_ENABLED")
    )
    r2_read_access_key_id: str = Field("", validation_alias=AliasChoices("R2_READ_ACCESS_KEY_ID"))
    r2_read_secret_access_key: str = Field(
        "", validation_alias=AliasChoices("R2_READ_SECRET_ACCESS_KEY")
    )
    r2_multi_bucket_max_scan_keys: int = Field(
        5000, validation_alias=AliasChoices("R2_MULTI_BUCKET_MAX_SCAN_KEYS")
    )
    r2_download_max_bytes: int = Field(
        512 * 1024 * 1024, validation_alias=AliasChoices("R2_DOWNLOAD_MAX_BYTES")
    )
    r2_required_read_lanes: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        validation_alias=AliasChoices("R2_REQUIRED_READ_LANES"),
    )

    # v1.6 ecosystem R2 lane registry. These envs let HIVE understand where
    # AIMS/RAMS/website/podcast artefacts live. Production now supports
    # controlled read/write operations across every configured lane when the
    # shared R2 write credentials and R2_MULTI_BUCKET_WRITE_ENABLED are enabled.
    r2_bucket_art: str = Field("", validation_alias=AliasChoices("R2_BUCKET_ART"))
    r2_bucket_audits: str = Field("", validation_alias=AliasChoices("R2_BUCKET_AUDITS"))
    r2_bucket_blog: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BLOG"))
    r2_bucket_blog_images: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BLOG_IMAGES"))
    r2_bucket_blog_rss: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BLOG_RSS"))
    r2_bucket_brand_assets: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BRAND_ASSETS"))
    r2_bucket_meta: str = Field("", validation_alias=AliasChoices("R2_BUCKET_META"))
    r2_bucket_meta_system: str = Field("", validation_alias=AliasChoices("R2_BUCKET_META_SYSTEM"))
    r2_bucket_podcast: str = Field("", validation_alias=AliasChoices("R2_BUCKET_PODCAST"))
    r2_bucket_podcast_rss_feeds: str = Field(
        "", validation_alias=AliasChoices("R2_BUCKET_PODCAST_RSS_FEEDS")
    )
    r2_bucket_rss_feeds: str = Field("", validation_alias=AliasChoices("R2_BUCKET_RSS_FEEDS"))
    r2_bucket_transcripts: str = Field("", validation_alias=AliasChoices("R2_BUCKET_TRANSCRIPTS"))
    r2_bucket_hive_skills: str = Field("", validation_alias=AliasChoices("R2_BUCKET_HIVE_SKILLS"))
    r2_bucket_repositories: str = Field(
        "", validation_alias=AliasChoices("R2_BUCKET_REPOSITORIES")
    )
    r2_public_base_url_repositories: str = Field(
        "",
        validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_REPOSITORIES"),
    )
    r2_public_base_url_art: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_ART"))
    r2_public_base_url_audits: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_AUDITS")
    )
    r2_public_base_url_blog: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BLOG")
    )
    r2_public_base_url_blog_images: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BLOG_IMAGES")
    )
    r2_public_base_url_blog_rss: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BLOG_RSS")
    )
    r2_public_base_url_brand_assets: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BRAND_ASSETS")
    )
    r2_public_base_url_meta: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_META")
    )
    r2_public_base_url_meta_system: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_META_SYSTEM")
    )
    r2_public_base_url_podcast: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_PODCAST")
    )
    r2_public_base_url_podcast_rss: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_PODCAST_RSS")
    )
    r2_public_base_url_rss: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_RSS"))
    r2_public_base_url_transcript: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_TRANSCRIPT")
    )
    r2_public_base_url_transcript_html: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_TRANSCRIPT_HTML")
    )
    r2_public_base_url_hive_skills: str = Field(
        "", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_HIVE_SKILLS")
    )

    # Phase 1 - Repository Intelligence. Uploaded repository ZIPs are extracted
    # into a per-process temp root and are never persisted permanently on
    # local disk. repository_ttl_seconds controls automatic cleanup of idle
    # working copies.
    repository_manager_enabled: bool = Field(
        True, validation_alias=AliasChoices("REPOSITORY_MANAGER_ENABLED")
    )
    repository_temp_dir: str = Field(
        "", validation_alias=AliasChoices("REPOSITORY_TEMP_DIR")
    )
    repository_ttl_seconds: int = Field(
        6 * 3600, validation_alias=AliasChoices("REPOSITORY_TTL_SECONDS")
    )
    repository_max_files: int = Field(
        20_000, validation_alias=AliasChoices("REPOSITORY_MAX_FILES")
    )
    repository_max_uncompressed_bytes: int = Field(
        512 * 1024 * 1024, validation_alias=AliasChoices("REPOSITORY_MAX_UNCOMPRESSED_BYTES")
    )

    # Optional SQL persistence. HIVE v1 works without this; enable when you want
    # conversation/message/file/cost records in SQLite or Koyeb/PostgreSQL.
    database_enabled: bool = Field(False, validation_alias=AliasChoices("DATABASE_ENABLED"))
    database_auto_init: bool = Field(True, validation_alias=AliasChoices("DATABASE_AUTO_INIT"))
    database_url: str = Field("", validation_alias=AliasChoices("DATABASE_URL", "DATABASE_URI"))
    database_host: str = Field("", validation_alias=AliasChoices("DATABASE_HOST", "POSTGRES_HOST"))
    database_port: int = Field(
        5432, validation_alias=AliasChoices("DATABASE_PORT", "POSTGRES_PORT")
    )
    database_user: str = Field("", validation_alias=AliasChoices("DATABASE_USER", "POSTGRES_USER"))
    database_password: str = Field(
        "", validation_alias=AliasChoices("DATABASE_PASSWORD", "POSTGRES_PASSWORD")
    )
    database_name: str = Field(
        "", validation_alias=AliasChoices("DATABASE_NAME", "POSTGRES_DB", "POSTGRES_DATABASE")
    )
    database_sslmode: str = "require"
    database_connect_timeout_seconds: int = Field(
        8, validation_alias=AliasChoices("DATABASE_CONNECT_TIMEOUT_SECONDS")
    )
    database_statement_timeout_seconds: int = Field(
        30, validation_alias=AliasChoices("DATABASE_STATEMENT_TIMEOUT_SECONDS")
    )

    # Optional Cloudflare D1 metadata store. D1 is kept separate from the SQL
    # conversation store so it can be used for ecosystem indexes/cache snapshots.
    d1_enabled: bool = Field(False, validation_alias=AliasChoices("D1_ENABLED"))
    d1_account_id: str = Field(
        "", validation_alias=AliasChoices("D1_ACCOUNT_ID", "CF_ACCOUNT_ID", "R2_ACCOUNT_ID")
    )
    d1_api_key: str = Field(
        "",
        validation_alias=AliasChoices(
            "D1_API_KEY", "D1_api_key", "D1_API_TOKEN", "CF_D1_API_TOKEN"
        ),
    )
    d1_database_id: str = Field("", validation_alias=AliasChoices("D1_DATABASE_ID", "D1_UUID"))
    d1_database_name: str = Field(
        "database-hive", validation_alias=AliasChoices("D1_DATABASE_NAME", "D1_DATABASE")
    )
    d1_timeout_seconds: int = Field(3, validation_alias=AliasChoices("D1_TIMEOUT_SECONDS"))
    d1_max_attempts: int = Field(1, validation_alias=AliasChoices("D1_MAX_ATTEMPTS"))

    # Optional Cloudflare Vectorize semantic retrieval. SQL chunks remain the
    # source of truth; Vectorize only stores/searches embeddings keyed by SQL chunk IDs.
    vectorize_enabled: bool = Field(False, validation_alias=AliasChoices("VECTORIZE_ENABLED"))
    vectorize_account_id: str = Field(
        "",
        validation_alias=AliasChoices(
            "VECTORIZE_ACCOUNT_ID", "CF_VECTORIZE_ACCOUNT_ID", "CF_ACCOUNT_ID", "R2_ACCOUNT_ID"
        ),
    )
    vectorize_api_token: str = Field(
        "",
        validation_alias=AliasChoices(
            "VECTORIZE_API_TOKEN", "Vectorize_API_kEY", "CF_VECTORIZE_API_TOKEN", "CF_API_TOKEN"
        ),
    )
    vectorize_index_name: str = Field(
        "hive-chunks", validation_alias=AliasChoices("VECTORIZE_INDEX_NAME", "CF_VECTORIZE_INDEX")
    )
    vectorize_timeout_seconds: int = Field(
        15, validation_alias=AliasChoices("VECTORIZE_TIMEOUT_SECONDS")
    )
    vectorize_max_attempts: int = Field(2, validation_alias=AliasChoices("VECTORIZE_MAX_ATTEMPTS"))
    vectorize_top_k: int = Field(8, validation_alias=AliasChoices("VECTORIZE_TOP_K"))
    vectorize_return_metadata: str | bool = Field(
        "all", validation_alias=AliasChoices("VECTORIZE_RETURN_METADATA")
    )

    embeddings_enabled: bool = Field(False, validation_alias=AliasChoices("EMBEDDINGS_ENABLED"))
    embeddings_provider: str = Field(
        "cloudflare", validation_alias=AliasChoices("EMBEDDINGS_PROVIDER")
    )
    embeddings_account_id: str = Field(
        "",
        validation_alias=AliasChoices(
            "EMBEDDINGS_ACCOUNT_ID", "VECTORIZE_ACCOUNT_ID", "CF_ACCOUNT_ID", "R2_ACCOUNT_ID"
        ),
    )
    embeddings_api_token: str = Field(
        "",
        validation_alias=AliasChoices(
            "EMBEDDINGS_API_TOKEN", "VECTORIZE_API_TOKEN", "Vectorize_API_kEY", "CF_API_TOKEN"
        ),
    )
    embeddings_model: str = Field(
        "@cf/baai/bge-base-en-v1.5",
        validation_alias=AliasChoices("EMBEDDINGS_MODEL", "CF_EMBEDDING_MODEL"),
    )
    embeddings_dimensions: int = Field(768, validation_alias=AliasChoices("EMBEDDINGS_DIMENSIONS"))
    embeddings_timeout_seconds: int = Field(
        20, validation_alias=AliasChoices("EMBEDDINGS_TIMEOUT_SECONDS")
    )
    embeddings_max_batch_size: int = Field(
        32, validation_alias=AliasChoices("EMBEDDINGS_MAX_BATCH_SIZE")
    )

    # Phase 2 - Repository Memory. Cloudflare AI Search lets Repository Memory
    # be queried semantically without loading a repository's full working
    # copy. Kept as its own adapter (distinct from Vectorize) since AI Search
    # indexes are managed per named instance rather than per-chunk-id.
    ai_search_enabled: bool = Field(False, validation_alias=AliasChoices("AI_SEARCH_ENABLED"))
    ai_search_account_id: str = Field(
        "",
        validation_alias=AliasChoices(
            "AI_SEARCH_ACCOUNT_ID", "CF_ACCOUNT_ID", "VECTORIZE_ACCOUNT_ID", "R2_ACCOUNT_ID"
        ),
    )
    ai_search_api_token: str = Field(
        "", validation_alias=AliasChoices("CF_WORKERS_AI_API", "AI_SEARCH_API_TOKEN")
    )
    ai_search_instance: str = Field(
        "hive-repositories", validation_alias=AliasChoices("AI_SEARCH_INSTANCE")
    )
    ai_search_timeout_seconds: int = Field(
        15, validation_alias=AliasChoices("AI_SEARCH_TIMEOUT_SECONDS")
    )
    ai_search_max_attempts: int = Field(2, validation_alias=AliasChoices("AI_SEARCH_MAX_ATTEMPTS"))
    ai_search_top_k: int = Field(8, validation_alias=AliasChoices("AI_SEARCH_TOP_K"))

    # Phase 3 - Model Registry. Optional JSON seed so ranked models per
    # category survive process restarts without requiring D1; the in-memory
    # ModelRegistry always takes precedence once populated at runtime.
    model_registry_seed_json: str = Field(
        "", validation_alias=AliasChoices("MODEL_REGISTRY_SEED_JSON")
    )

    # Phase 4 - Provider Framework. OpenRouter is always discovered when
    # openrouter_api_key is configured. Additional OpenRouter-compatible
    # providers (same /models shape) can be added purely via this JSON list,
    # e.g. '[{"name": "example", "base_url": "https://example.ai/api/v1",
    # "api_token": "..."}]', without any new provider code.
    provider_framework_extra_providers_json: str = Field(
        "", validation_alias=AliasChoices("PROVIDER_FRAMEWORK_EXTRA_PROVIDERS_JSON")
    )

    # Phase 6 - Benchmark Engine. Optional override of the default per-metric
    # weighting (coding/reasoning benchmarks, cost, latency, reliability,
    # long-context, JSON reliability, structured output, community maturity,
    # internal historical performance). Leave empty to use built-in defaults.
    benchmark_weights_json: str = Field(
        "", validation_alias=AliasChoices("BENCHMARK_WEIGHTS_JSON")
    )

    # Phase 5 - AI Council. Monthly (or on-demand) discovery/benchmark/
    # promotion run across all configured providers.
    ai_council_promotion_threshold: float = Field(
        0.72, validation_alias=AliasChoices("AI_COUNCIL_PROMOTION_THRESHOLD")
    )
    ai_council_coding_keywords: str = Field(
        "code,coder,coding,dev,program",
        validation_alias=AliasChoices("AI_COUNCIL_CODING_KEYWORDS"),
    )

    # Phase 10 - Connector Framework. GitHub is one of the four initial
    # connectors (OpenRouter, Cloudflare R2, Cloudflare AI Search, GitHub).
    github_token: str = Field("", validation_alias=AliasChoices("GITHUB_TOKEN"))
    github_repository: str = Field(
        "", validation_alias=AliasChoices("GITHUB_REPOSITORY")
    )

    # Backwards-compatible aliases for older adapter references.
    @property
    def cf_account_id(self) -> str:
        return self.vectorize_account_id

    @property
    def cf_api_token(self) -> str:
        return self.vectorize_api_token

    @property
    def cf_vectorize_index(self) -> str:
        return self.vectorize_index_name

    @property
    def cf_embedding_model(self) -> str:
        return self.embeddings_model

    hive_free_tier_mode: bool = Field(
        True, validation_alias=AliasChoices("HIVE_FREE_TIER_MODE", "KOYEB_FREE_TIER_MODE")
    )
    max_upload_bytes: int = Field(
        100 * 1024 * 1024, validation_alias=AliasChoices("MAX_UPLOAD_BYTES")
    )
    max_zip_files: int = Field(5000, validation_alias=AliasChoices("MAX_ZIP_FILES"))
    max_zip_uncompressed_bytes: int = Field(
        500 * 1024 * 1024, validation_alias=AliasChoices("MAX_ZIP_UNCOMPRESSED_BYTES")
    )
    max_file_read_bytes: int = Field(
        2 * 1024 * 1024, validation_alias=AliasChoices("MAX_FILE_READ_BYTES")
    )
    max_file_chat_chars: int = Field(24_000, validation_alias=AliasChoices("MAX_FILE_CHAT_CHARS"))
    file_chunk_max_chars: int = Field(4000, validation_alias=AliasChoices("FILE_CHUNK_MAX_CHARS"))
    file_chunk_overlap_chars: int = Field(
        400, validation_alias=AliasChoices("FILE_CHUNK_OVERLAP_CHARS")
    )
    file_chunk_max_count: int = Field(500, validation_alias=AliasChoices("FILE_CHUNK_MAX_COUNT"))
    file_retrieval_max_chunks: int = Field(
        6, validation_alias=AliasChoices("FILE_RETRIEVAL_MAX_CHUNKS")
    )

    document_extract_max_chars: int = Field(
        120_000, validation_alias=AliasChoices("DOCUMENT_EXTRACT_MAX_CHARS")
    )
    document_extract_pdf_max_pages: int = Field(
        40, validation_alias=AliasChoices("DOCUMENT_EXTRACT_PDF_MAX_PAGES")
    )
    document_extract_csv_max_rows: int = Field(
        2000, validation_alias=AliasChoices("DOCUMENT_EXTRACT_CSV_MAX_ROWS")
    )
    document_extract_xlsx_max_rows_per_sheet: int = Field(
        500, validation_alias=AliasChoices("DOCUMENT_EXTRACT_XLSX_MAX_ROWS_PER_SHEET")
    )
    document_extract_xlsx_max_sheets: int = Field(
        12, validation_alias=AliasChoices("DOCUMENT_EXTRACT_XLSX_MAX_SHEETS")
    )
    document_extract_docx_max_table_rows: int = Field(
        2000, validation_alias=AliasChoices("DOCUMENT_EXTRACT_DOCX_MAX_TABLE_ROWS")
    )
    zip_extract_max_members: int = Field(
        160, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_MEMBERS")
    )
    zip_extract_max_member_bytes: int = Field(
        2 * 1024 * 1024, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_MEMBER_BYTES")
    )
    zip_extract_max_total_text_chars: int = Field(
        120_000, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_TOTAL_TEXT_CHARS")
    )
    zip_extract_max_depth: int = Field(2, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_DEPTH"))
    zip_extract_supported_suffixes: str = Field(
        ".txt,.md,.mdx,.rst,.adoc,.log,.json,.jsonl,.jsonc,.csv,.tsv,.html,.htm,.xml,.rss,.svg,.yaml,.yml,.toml,.ini,.cfg,.conf,.properties,.lock,.lockb,.env,.py,.pyi,.js,.mjs,.cjs,.ts,.tsx,.jsx,.mts,.cts,.css,.scss,.sass,.less,.vue,.svelte,.astro,.sh,.bash,.zsh,.fish,.ps1,.bat,.cmd,.sql,.graphql,.gql,.proto,.tf,.tfvars,.hcl,.go,.mod,.sum,.rs,.rb,.php,.java,.kt,.kts,.swift,.c,.h,.cpp,.cxx,.hpp,.cs,.fs,.fsx,.r,.lua,.pl,.pm,.scala,.sbt,.gradle,.prisma,.ipynb,.pdf,.docx,.xlsx",
        validation_alias=AliasChoices("ZIP_EXTRACT_SUPPORTED_SUFFIXES"),
    )
    zip_extract_supported_filenames: str = Field(
        ".gitignore,.gitattributes,.gitmodules,.dockerignore,.editorconfig,.npmrc,.nvmrc,.prettierrc,.prettierignore,.eslintrc,.eslintignore,.python-version,.ruby-version,.tool-versions,.env,.env.example,.env.local,.env.production,.env.development,.env.staging,.env.test,Dockerfile,Dockerfile.dev,Dockerfile.prod,Dockerfile.production,Makefile,Procfile,README,LICENSE,NOTICE,COPYING,CHANGELOG,CONTRIBUTING,SECURITY,CODEOWNERS,OWNERS,AUTHORS,requirements,Pipfile,Gemfile,Rakefile,Brewfile,Caddyfile,Jenkinsfile,justfile",
        validation_alias=AliasChoices("ZIP_EXTRACT_SUPPORTED_FILENAMES"),
    )

    # v1.8 shared skill pool importer. Bounded for production; skills live in R2 and D1 stores catalogue metadata.
    skill_registry_import_max_items: int = Field(
        250, validation_alias=AliasChoices("SKILL_REGISTRY_IMPORT_MAX_ITEMS")
    )
    skill_registry_import_timeout_seconds: int = Field(
        6, validation_alias=AliasChoices("SKILL_REGISTRY_IMPORT_TIMEOUT_SECONDS")
    )
    skill_registry_max_source_bytes: int = Field(
        5 * 1024 * 1024, validation_alias=AliasChoices("SKILL_REGISTRY_MAX_SOURCE_BYTES")
    )
    skill_registry_fallback_enabled: bool = Field(
        True, validation_alias=AliasChoices("SKILL_REGISTRY_FALLBACK_ENABLED")
    )
    skill_registry_fallback_cache_seconds: int = Field(
        300, validation_alias=AliasChoices("SKILL_REGISTRY_FALLBACK_CACHE_SECONDS")
    )
    skill_context_enabled: bool = Field(
        True, validation_alias=AliasChoices("SKILL_CONTEXT_ENABLED")
    )
    skill_context_max_items: int = Field(
        2, validation_alias=AliasChoices("SKILL_CONTEXT_MAX_ITEMS")
    )
    skill_context_max_chars: int = Field(
        4000, validation_alias=AliasChoices("SKILL_CONTEXT_MAX_CHARS")
    )
    skill_context_risk_ceiling: str = Field(
        "medium", validation_alias=AliasChoices("SKILL_CONTEXT_RISK_CEILING")
    )

    @field_validator(
        "admin_bearer_token",
        "ops_event_ingest_token",
        "rams_health_bearer_token",
        "rams_readiness_bearer_token",
        "rams_qa_ingest_token",
        "openrouter_api_key",
        "cf_r2_access_key_id",
        "cf_r2_secret_access_key",
        "r2_read_access_key_id",
        "r2_read_secret_access_key",
        "database_password",
        "d1_api_key",
        "vectorize_api_token",
        "embeddings_api_token",
        "ai_search_api_token",
        "github_token",
        mode="before",
    )
    @classmethod
    def clean_secret_placeholder(cls, value: object) -> str:
        secret = str(value or "").strip().strip('"').strip("'")
        compact = secret.replace(" ", "")
        if compact.startswith("{{secret.") and compact.endswith("}}"):
            return ""
        return secret

    @field_validator("embeddings_model", mode="before")
    @classmethod
    def normalise_embeddings_model(cls, value: object) -> str:
        """Accept Koyeb-safe Workers AI model values and restore ``@cf``.

        Koyeb's environment editor rejects a value that begins with ``@``.
        Operators can therefore configure ``cf/...`` while HIVE presents the
        canonical Cloudflare Workers AI identifier internally.
        """

        model = str(value or "").strip().strip('"').strip("'").lstrip("/")
        if model.startswith("cf/"):
            return f"@{model}"
        return model

    @field_validator("cors_origins", "allowed_hosts", "r2_required_read_lanes", mode="before")
    @classmethod
    def parse_comma_separated_list(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local", "test"}

    @property
    def expose_api_docs(self) -> bool:
        return self.is_dev or self.api_docs_enabled

    @property
    def effective_allowed_hosts(self) -> list[str]:
        hosts = [item.strip() for item in self.allowed_hosts if item.strip()]
        return hosts or ["*"]

    @property
    def sql_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        if self.database_host and self.database_user and self.database_name:
            from urllib.parse import quote_plus

            user = quote_plus(self.database_user)
            password = quote_plus(self.database_password)
            auth = f"{user}:{password}" if password else user
            port = f":{self.database_port}" if self.database_port else ""
            ssl = f"?sslmode={quote_plus(self.database_sslmode)}" if self.database_sslmode else ""
            return f"postgresql://{auth}@{self.database_host}{port}/{self.database_name}{ssl}"
        if self.database_enabled and self.is_dev:
            return "sqlite:///./local-data/hive.sqlite3"
        return ""

    @property
    def r2_endpoint_url(self) -> str:
        if self.cf_r2_endpoint_url:
            return self.cf_r2_endpoint_url.strip().rstrip("/")
        if not self.cf_r2_account_id:
            return ""
        return f"https://{self.cf_r2_account_id.strip()}.r2.cloudflarestorage.com"

    _HIDDEN_R2_LANES: frozenset[str] = frozenset({"meta_system"})

    @property
    def r2_ecosystem_lanes(self) -> list[dict[str, Any]]:
        # Lanes listed in _HIDDEN_R2_LANES are hidden from every consumer of this
        # property: the /ecosystem endpoint and the file API's per-lane
        # list/read/write routes. This mirrors the HIDDEN_BUCKETS registry in
        # services/bucket_manager.py. Internal readiness/monitoring code (e.g.
        # core/production.py, the MAST heartbeat reader in repo_health.py) must
        # resolve hidden lanes too, so it uses `r2_all_lanes`/`r2_lane()` below,
        # which deliberately do NOT apply this filter.
        return [
            item for item in self._r2_lane_registry() if item["lane"] not in self._HIDDEN_R2_LANES
        ]

    @property
    def r2_all_lanes(self) -> list[dict[str, Any]]:
        """Full R2 lane registry, including internal-only lanes such as
        `meta_system`. For internal readiness/monitoring resolution only -
        never expose this via a user-facing endpoint; use `r2_ecosystem_lanes`
        for anything reachable by the UI or file API."""
        return self._r2_lane_registry()

    def _r2_lane_registry(self) -> list[dict[str, Any]]:
        lanes = [
            (

                "uploads",
                self.cf_r2_bucket,
                self.cf_r2_public_base_url,
                "Primary HIVE upload/read bucket",
            ),
            (
                "audits",
                self.r2_bucket_audits,
                self.r2_public_base_url_audits,
                "RAMS/AIMS audit reports",
            ),
            ("art", self.r2_bucket_art, self.r2_public_base_url_art, "Podcast cover art"),
            ("blog", self.r2_bucket_blog, self.r2_public_base_url_blog, "Published blog artefacts"),
            (
                "blog_images",
                self.r2_bucket_blog_images,
                self.r2_public_base_url_blog_images,
                "Blog imagery",
            ),
            (
                "blog_rss",
                self.r2_bucket_blog_rss,
                self.r2_public_base_url_blog_rss,
                "Blog RSS feed",
            ),
            (
                "brand_assets",
                self.r2_bucket_brand_assets,
                self.r2_public_base_url_brand_assets,
                "Shared brand assets",
            ),
            ("meta", self.r2_bucket_meta, self.r2_public_base_url_meta, "Podcast metadata"),
            (
                "meta_system",
                self.r2_bucket_meta_system,
                self.r2_public_base_url_meta_system,
                "System metadata",
            ),
            (
                "podcast",
                self.r2_bucket_podcast,
                self.r2_public_base_url_podcast,
                "Podcast audio/pages",
            ),
            (
                "podcast_rss",
                self.r2_bucket_podcast_rss_feeds,
                self.r2_public_base_url_podcast_rss,
                "Podcast RSS feeds",
            ),
            ("rss", self.r2_bucket_rss_feeds, self.r2_public_base_url_rss, "AI/news RSS feeds"),
            (
                "repositories",
                self.r2_bucket_repositories,
                self.r2_public_base_url_repositories,
                "Repository Manager temporary extraction archives",
            ),
            (
                "transcripts",
                self.r2_bucket_transcripts,
                self.r2_public_base_url_transcript,
                "Podcast transcripts",
            ),
            (
                "transcript_html",
                self.r2_bucket_transcripts,
                self.r2_public_base_url_transcript_html,
                "Transcript HTML mirror",
            ),
            (
                "hive_skills",
                self.r2_bucket_hive_skills,
                self.r2_public_base_url_hive_skills,
                "Shared HIVE/AIMS/RAMS skills pool",
            ),
        ]
        payload: list[dict[str, Any]] = []
        write_credentials_configured = bool(
            self.r2_endpoint_url
            and self.cf_r2_access_key_id
            and self.cf_r2_secret_access_key
        )
        read_credentials_configured = bool(
            write_credentials_configured
            or (
                self.r2_multi_bucket_read_enabled
                and self.r2_endpoint_url
                and self.r2_read_access_key_id
                and self.r2_read_secret_access_key
            )
        )
        for name, bucket, public_base_url, description in lanes:
            primary = name == "uploads"
            configured = bool(bucket or public_base_url)
            readable = bool(bucket) and read_credentials_configured
            writable = bool(bucket) and write_credentials_configured and (
                primary or self.r2_multi_bucket_write_enabled
            )
            if writable:
                access_mode = "read_write"
            elif readable:
                access_mode = "read_only"
            elif configured:
                access_mode = "registry_only"
            else:
                access_mode = "unavailable"
            payload.append(
                {
                    "lane": name,
                    "bucket": bucket or None,
                    "public_base_url": public_base_url.rstrip("/") if public_base_url else None,
                    "configured": configured,
                    "description": description,
                    "primary_upload_lane": primary,
                    "readable": readable,
                    "writable": writable,
                    "access_mode": access_mode,
                    "chat_supported": readable,
                }
            )
        return payload

    def r2_lane(self, lane: str) -> dict[str, Any] | None:
        """Resolve a lane name against the user/UI-facing registry only.

        This is safe to call with untrusted input (e.g. a `lane` path param from
        the file API): hidden internal lanes such as `meta_system` will never
        resolve here. Internal callers that legitimately need a hidden lane
        (driven only by operator-configured env vars, never by a request) must
        use `internal_r2_lane()` instead.
        """
        clean_lane = (lane or "").strip().lower().replace("-", "_")
        aliases = {
            "upload": "uploads",
            "skills": "hive_skills",
            "podcast_rss_feeds": "podcast_rss",
            "rss_feeds": "rss",
            "transcript": "transcripts",
        }
        clean_lane = aliases.get(clean_lane, clean_lane)
        for item in self.r2_ecosystem_lanes:
            if item["lane"] == clean_lane:
                return item
        return None

    def internal_r2_lane(self, lane: str) -> dict[str, Any] | None:
        """Resolve a lane name against the full registry, including hidden
        internal-only lanes (e.g. `meta_system`). Only call this with
        operator-configured lane names (settings/env values); never with
        user-supplied input, or a hidden lane could become reachable."""
        clean_lane = (lane or "").strip().lower().replace("-", "_")
        aliases = {
            "upload": "uploads",
            "skills": "hive_skills",
            "podcast_rss_feeds": "podcast_rss",
            "rss_feeds": "rss",
            "transcript": "transcripts",
        }
        clean_lane = aliases.get(clean_lane, clean_lane)
        for item in self.r2_all_lanes:
            if item["lane"] == clean_lane:
                return item
        return None

    @property
    def r2_read_credentials_configured(self) -> bool:
        return bool(
            self.r2_write_credentials_configured
            or (
                self.r2_multi_bucket_read_enabled
                and self.r2_endpoint_url
                and self.r2_read_access_key_id
                and self.r2_read_secret_access_key
            )
        )

    @property
    def r2_write_credentials_configured(self) -> bool:
        return bool(
            self.r2_endpoint_url
            and self.cf_r2_access_key_id
            and self.cf_r2_secret_access_key
        )

    def public_url_for_r2_lane(self, lane: str, key: str) -> str | None:
        """Resolve a public object URL against the user/UI-facing registry only.

        Safe for untrusted `lane` input; hidden lanes (e.g. `meta_system`) never
        resolve here. See `internal_public_url_for_r2_lane()` for operator-config
        driven lookups that must be able to reach hidden lanes.
        """
        return self._public_url_for_lane(lane, key, self.r2_ecosystem_lanes)

    def internal_public_url_for_r2_lane(self, lane: str, key: str) -> str | None:
        """Like `public_url_for_r2_lane()`, but also resolves hidden internal-only
        lanes. Only call this with operator-configured lane names; never with
        user-supplied input."""
        return self._public_url_for_lane(lane, key, self.r2_all_lanes)

    @staticmethod
    def _public_url_for_lane(lane: str, key: str, lanes: list[dict[str, Any]]) -> str | None:
        clean_lane = (lane or "").strip().lower().replace("-", "_")
        clean_key = (key or "").replace("\\", "/").lstrip("/")
        decoded_key = unquote(clean_key)
        if any(part in {"", ".", ".."} for part in decoded_key.split("/")) and clean_key:
            return None
        encoded_key = quote(clean_key, safe="/~")
        for item in lanes:
            if item["lane"] == clean_lane:
                base = item.get("public_base_url")
                return f"{base}/{encoded_key}" if base and encoded_key else base
        return None

    @property
    def required_r2_read_lane_names(self) -> list[str]:
        """Return canonical lanes that must be readable for production readiness.

        An explicit R2_REQUIRED_READ_LANES value wins. When it is omitted in
        production, every configured bucket lane is required so readiness cannot
        report green while a governed lane is registry-only.
        """

        requested = [
            item.strip().lower().replace("-", "_")
            for item in self.r2_required_read_lanes
            if item.strip()
        ]
        if not requested and not self.is_dev and self.production_require_r2:
            requested = [
                str(item["lane"]) for item in self.r2_all_lanes if item.get("bucket")
            ]
        canonical: list[str] = []
        for name in requested:
            # This list is entirely operator-configured (R2_REQUIRED_READ_LANES or
            # every bucket lane in production), never user input, so it is safe -
            # and necessary - to resolve against the full registry here.
            lane = self.internal_r2_lane(name)
            if lane and lane["lane"] not in canonical:
                canonical.append(str(lane["lane"]))
            elif not lane and name not in canonical:
                canonical.append(name)
        return canonical

    @property
    def configured_r2_ecosystem_lane_count(self) -> int:
        return sum(1 for item in self.r2_ecosystem_lanes if item.get("configured"))

    @property
    def zip_extract_supported_suffix_set(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.zip_extract_supported_suffixes.split(",")
            if item.strip()
        }

    @property
    def zip_extract_supported_filename_set(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.zip_extract_supported_filenames.split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
