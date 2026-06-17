from functools import lru_cache
from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)

    app_name: str = Field("JH Ops Chat", validation_alias=AliasChoices("APP_NAME", "OPENROUTER_APP_NAME"))
    app_env: str = Field("development", validation_alias=AliasChoices("APP_ENV"))
    app_version: str = Field("1.24.0-production", validation_alias=AliasChoices("APP_VERSION"))
    admin_bearer_token: str = Field("change-me-local-only", validation_alias=AliasChoices("ADMIN_BEARER_TOKEN"))
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"], validation_alias=AliasChoices("CORS_ORIGINS"))
    allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"], validation_alias=AliasChoices("ALLOWED_HOSTS"))
    api_docs_enabled: bool = Field(False, validation_alias=AliasChoices("API_DOCS_ENABLED"))
    security_headers_enabled: bool = Field(True, validation_alias=AliasChoices("SECURITY_HEADERS_ENABLED"))
    request_logging_enabled: bool = Field(True, validation_alias=AliasChoices("REQUEST_LOGGING_ENABLED"))
    trusted_hosts_enabled: bool = Field(True, validation_alias=AliasChoices("TRUSTED_HOSTS_ENABLED"))
    max_request_body_bytes: int = Field(110 * 1024 * 1024, validation_alias=AliasChoices("MAX_REQUEST_BODY_BYTES"))
    production_require_openrouter: bool = Field(True, validation_alias=AliasChoices("PRODUCTION_REQUIRE_OPENROUTER"))
    production_require_r2: bool = Field(True, validation_alias=AliasChoices("PRODUCTION_REQUIRE_R2"))
    production_require_database: bool = Field(False, validation_alias=AliasChoices("PRODUCTION_REQUIRE_DATABASE"))

    # Read-only ecosystem service monitoring for the HIVE-UI Ops dashboard.
    # Targets are operator-controlled environment values; request callers cannot
    # supply arbitrary URLs. Results are redacted, bounded and cached.
    repo_health_enabled: bool = Field(True, validation_alias=AliasChoices("REPO_HEALTH_ENABLED"))
    repo_health_timeout_seconds: float = Field(6.0, validation_alias=AliasChoices("REPO_HEALTH_TIMEOUT_SECONDS"))
    repo_health_cache_seconds: int = Field(30, validation_alias=AliasChoices("REPO_HEALTH_CACHE_SECONDS"))

    # Central operational event inbox used by GitHub, provider deployment watchers
    # and runtime services. The ingest token is separate from the HIVE admin token.
    ops_event_ingest_enabled: bool = Field(False, validation_alias=AliasChoices("OPS_EVENT_INGEST_ENABLED"))
    ops_event_ingest_token: str = Field("", validation_alias=AliasChoices("OPS_EVENT_INGEST_TOKEN"))
    ops_event_memory_limit: int = Field(200, ge=10, le=2000, validation_alias=AliasChoices("OPS_EVENT_MEMORY_LIMIT"))

    hive_ui_health_url: str = Field("https://hive.jonathan-harris.online/health", validation_alias=AliasChoices("HIVE_UI_HEALTH_URL"))
    aims_health_url: str = Field("https://app.jonathan-harris.online/health", validation_alias=AliasChoices("AIMS_HEALTH_URL"))
    aims_operational_health_url: str = Field("https://app.jonathan-harris.online/ops/health", validation_alias=AliasChoices("AIMS_OPERATIONAL_HEALTH_URL"))
    rams_health_url: str = Field("https://mod.jonathan-harris.online/health", validation_alias=AliasChoices("RAMS_HEALTH_URL"))
    rams_readiness_url: str = Field("https://mod.jonathan-harris.online/readiness", validation_alias=AliasChoices("RAMS_READINESS_URL"))
    rams_health_bearer_token: str = Field("", validation_alias=AliasChoices("RAMS_HEALTH_BEARER_TOKEN", "RMS_API_KEY"))
    mast_monitor_mode: str = Field("r2", validation_alias=AliasChoices("MAST_MONITOR_MODE"))
    mast_health_url: str = Field("", validation_alias=AliasChoices("MAST_HEALTH_URL"))
    mast_status_url: str = Field("", validation_alias=AliasChoices("MAST_STATUS_URL"))
    mast_state_object_key: str = Field("state/mast/scheduler-state.json", validation_alias=AliasChoices("MAST_STATE_OBJECT_KEY"))
    mast_state_healthy_max_age_seconds: int = Field(90, ge=10, le=3600, validation_alias=AliasChoices("MAST_STATE_HEALTHY_MAX_AGE_SECONDS"))
    mast_state_down_max_age_seconds: int = Field(300, ge=30, le=86400, validation_alias=AliasChoices("MAST_STATE_DOWN_MAX_AGE_SECONDS"))
    mast_failure_degraded_threshold: int = Field(3, ge=1, le=100, validation_alias=AliasChoices("MAST_FAILURE_DEGRADED_THRESHOLD"))
    irs_health_url: str = Field("https://images.jonathan-harris.online/health.json", validation_alias=AliasChoices("IRS_HEALTH_URL"))
    website_health_url: str = Field("https://jonathan-harris.online/health.json", validation_alias=AliasChoices("WEBSITE_HEALTH_URL"))

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://jonathan-harris.online"
    openrouter_app_title: str = Field("JH Ops Chat", validation_alias=AliasChoices("OPENROUTER_APP_TITLE", "OPENROUTER_APP_NAME"))

    # Model policy is deliberately env-driven. Defaults use currently common
    # OpenRouter aliases / cheap high-context models rather than brittle dated IDs.
    default_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    cheap_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    balanced_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    premium_model: str = "~anthropic/claude-sonnet-latest"
    code_model: str = "x-ai/grok-build-0.1"
    audit_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_free_fallback_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    allow_paid_fallback: bool = False
    openrouter_model_preflight_enabled: bool = True
    openrouter_model_list_timeout_seconds: float = 10
    openrouter_attempt_timeout_seconds: float = 12
    openrouter_max_fallback_attempts: int = 2
    openrouter_empty_reply_retry_enabled: bool = True
    openrouter_min_response_tokens: int = 80
    chat_with_file_model_timeout_seconds: float = 30

    cf_r2_account_id: str = Field("", validation_alias=AliasChoices("CF_R2_ACCOUNT_ID", "R2_ACCOUNT_ID"))
    cf_r2_access_key_id: str = Field("", validation_alias=AliasChoices("CF_R2_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID"))
    cf_r2_secret_access_key: str = Field("", validation_alias=AliasChoices("CF_R2_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY"))
    cf_r2_bucket: str = Field("ops-chat-uploads", validation_alias=AliasChoices("CF_R2_BUCKET", "R2_BUCKET_UPLOADS", "R2_BUCKET"))
    cf_r2_public_base_url: str = Field("", validation_alias=AliasChoices("CF_R2_PUBLIC_BASE_URL", "R2_PUBLIC_BASE_URL"))
    cf_r2_endpoint_url: str = Field("", validation_alias=AliasChoices("CF_R2_ENDPOINT_URL", "R2_ENDPOINT_URL"))
    r2_region: str = Field("auto", validation_alias=AliasChoices("R2_REGION", "AWS_REGION"))
    r2_connect_timeout_seconds: int = 8
    r2_read_timeout_seconds: int = 20
    r2_max_attempts: int = 2
    r2_addressing_style: str = "path"
    r2_multi_bucket_read_enabled: bool = Field(False, validation_alias=AliasChoices("R2_MULTI_BUCKET_READ_ENABLED"))
    r2_read_access_key_id: str = Field("", validation_alias=AliasChoices("R2_READ_ACCESS_KEY_ID"))
    r2_read_secret_access_key: str = Field("", validation_alias=AliasChoices("R2_READ_SECRET_ACCESS_KEY"))
    r2_multi_bucket_max_scan_keys: int = Field(5000, validation_alias=AliasChoices("R2_MULTI_BUCKET_MAX_SCAN_KEYS"))
    r2_download_max_bytes: int = Field(512 * 1024 * 1024, validation_alias=AliasChoices("R2_DOWNLOAD_MAX_BYTES"))

    # v1.6 ecosystem R2 lane registry. These envs let HIVE understand where
    # AIMS/RAMS/website/podcast artefacts live without granting new write paths.
    r2_bucket_art: str = Field("", validation_alias=AliasChoices("R2_BUCKET_ART"))
    r2_bucket_audits: str = Field("", validation_alias=AliasChoices("R2_BUCKET_AUDITS"))
    r2_bucket_blog: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BLOG"))
    r2_bucket_blog_images: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BLOG_IMAGES"))
    r2_bucket_blog_rss: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BLOG_RSS"))
    r2_bucket_brand_assets: str = Field("", validation_alias=AliasChoices("R2_BUCKET_BRAND_ASSETS"))
    r2_bucket_meta: str = Field("", validation_alias=AliasChoices("R2_BUCKET_META"))
    r2_bucket_meta_system: str = Field("", validation_alias=AliasChoices("R2_BUCKET_META_SYSTEM"))
    r2_bucket_podcast: str = Field("", validation_alias=AliasChoices("R2_BUCKET_PODCAST"))
    r2_bucket_podcast_rss_feeds: str = Field("", validation_alias=AliasChoices("R2_BUCKET_PODCAST_RSS_FEEDS"))
    r2_bucket_rss_feeds: str = Field("", validation_alias=AliasChoices("R2_BUCKET_RSS_FEEDS"))
    r2_bucket_transcripts: str = Field("", validation_alias=AliasChoices("R2_BUCKET_TRANSCRIPTS"))
    r2_bucket_hive_skills: str = Field("", validation_alias=AliasChoices("R2_BUCKET_HIVE_SKILLS"))
    r2_public_base_url_art: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_ART"))
    r2_public_base_url_audits: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_AUDITS"))
    r2_public_base_url_blog: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BLOG"))
    r2_public_base_url_blog_images: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BLOG_IMAGES"))
    r2_public_base_url_blog_rss: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BLOG_RSS"))
    r2_public_base_url_brand_assets: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_BRAND_ASSETS"))
    r2_public_base_url_meta: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_META"))
    r2_public_base_url_meta_system: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_META_SYSTEM"))
    r2_public_base_url_podcast: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_PODCAST"))
    r2_public_base_url_podcast_rss: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_PODCAST_RSS"))
    r2_public_base_url_rss: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_RSS"))
    r2_public_base_url_transcript: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_TRANSCRIPT"))
    r2_public_base_url_transcript_html: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_TRANSCRIPT_HTML"))
    r2_public_base_url_hive_skills: str = Field("", validation_alias=AliasChoices("R2_PUBLIC_BASE_URL_HIVE_SKILLS"))

    # Optional SQL persistence. HIVE v1 works without this; enable when you want
    # conversation/message/file/cost records in SQLite or Koyeb/PostgreSQL.
    database_enabled: bool = Field(False, validation_alias=AliasChoices("DATABASE_ENABLED"))
    database_url: str = Field("", validation_alias=AliasChoices("DATABASE_URL", "DATABASE_URI"))
    database_host: str = Field("", validation_alias=AliasChoices("DATABASE_HOST", "POSTGRES_HOST"))
    database_port: int = Field(5432, validation_alias=AliasChoices("DATABASE_PORT", "POSTGRES_PORT"))
    database_user: str = Field("", validation_alias=AliasChoices("DATABASE_USER", "POSTGRES_USER"))
    database_password: str = Field("", validation_alias=AliasChoices("DATABASE_PASSWORD", "POSTGRES_PASSWORD"))
    database_name: str = Field("", validation_alias=AliasChoices("DATABASE_NAME", "POSTGRES_DB", "POSTGRES_DATABASE"))
    database_sslmode: str = "require"
    database_connect_timeout_seconds: int = Field(8, validation_alias=AliasChoices("DATABASE_CONNECT_TIMEOUT_SECONDS"))
    database_statement_timeout_seconds: int = Field(30, validation_alias=AliasChoices("DATABASE_STATEMENT_TIMEOUT_SECONDS"))

    # Optional Cloudflare D1 metadata store. D1 is kept separate from the SQL
    # conversation store so it can be used for ecosystem indexes/cache snapshots.
    d1_enabled: bool = Field(False, validation_alias=AliasChoices("D1_ENABLED"))
    d1_account_id: str = Field("", validation_alias=AliasChoices("D1_ACCOUNT_ID", "CF_ACCOUNT_ID", "R2_ACCOUNT_ID"))
    d1_api_key: str = Field("", validation_alias=AliasChoices("D1_API_KEY", "D1_api_key", "D1_API_TOKEN", "CF_D1_API_TOKEN"))
    d1_database_id: str = Field("", validation_alias=AliasChoices("D1_DATABASE_ID", "D1_UUID"))
    d1_database_name: str = Field("database-hive", validation_alias=AliasChoices("D1_DATABASE_NAME", "D1_DATABASE"))
    d1_timeout_seconds: int = Field(12, validation_alias=AliasChoices("D1_TIMEOUT_SECONDS"))
    d1_max_attempts: int = Field(2, validation_alias=AliasChoices("D1_MAX_ATTEMPTS"))

    # Optional Cloudflare Vectorize semantic retrieval. SQL chunks remain the
    # source of truth; Vectorize only stores/searches embeddings keyed by SQL chunk IDs.
    vectorize_enabled: bool = Field(False, validation_alias=AliasChoices("VECTORIZE_ENABLED"))
    vectorize_account_id: str = Field("", validation_alias=AliasChoices("VECTORIZE_ACCOUNT_ID", "CF_VECTORIZE_ACCOUNT_ID", "CF_ACCOUNT_ID", "R2_ACCOUNT_ID"))
    vectorize_api_token: str = Field("", validation_alias=AliasChoices("VECTORIZE_API_TOKEN", "Vectorize_API_kEY", "CF_VECTORIZE_API_TOKEN", "CF_API_TOKEN"))
    vectorize_index_name: str = Field("hive-chunks", validation_alias=AliasChoices("VECTORIZE_INDEX_NAME", "CF_VECTORIZE_INDEX"))
    vectorize_timeout_seconds: int = Field(15, validation_alias=AliasChoices("VECTORIZE_TIMEOUT_SECONDS"))
    vectorize_max_attempts: int = Field(2, validation_alias=AliasChoices("VECTORIZE_MAX_ATTEMPTS"))
    vectorize_top_k: int = Field(8, validation_alias=AliasChoices("VECTORIZE_TOP_K"))
    vectorize_return_metadata: str | bool = Field("all", validation_alias=AliasChoices("VECTORIZE_RETURN_METADATA"))

    embeddings_enabled: bool = Field(False, validation_alias=AliasChoices("EMBEDDINGS_ENABLED"))
    embeddings_provider: str = Field("cloudflare", validation_alias=AliasChoices("EMBEDDINGS_PROVIDER"))
    embeddings_account_id: str = Field("", validation_alias=AliasChoices("EMBEDDINGS_ACCOUNT_ID", "VECTORIZE_ACCOUNT_ID", "CF_ACCOUNT_ID", "R2_ACCOUNT_ID"))
    embeddings_api_token: str = Field("", validation_alias=AliasChoices("EMBEDDINGS_API_TOKEN", "VECTORIZE_API_TOKEN", "Vectorize_API_kEY", "CF_API_TOKEN"))
    embeddings_model: str = Field("@cf/baai/bge-base-en-v1.5", validation_alias=AliasChoices("EMBEDDINGS_MODEL", "CF_EMBEDDING_MODEL"))
    embeddings_dimensions: int = Field(768, validation_alias=AliasChoices("EMBEDDINGS_DIMENSIONS"))
    embeddings_timeout_seconds: int = Field(20, validation_alias=AliasChoices("EMBEDDINGS_TIMEOUT_SECONDS"))
    embeddings_max_batch_size: int = Field(32, validation_alias=AliasChoices("EMBEDDINGS_MAX_BATCH_SIZE"))

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

    hive_free_tier_mode: bool = Field(True, validation_alias=AliasChoices("HIVE_FREE_TIER_MODE", "KOYEB_FREE_TIER_MODE"))
    max_upload_bytes: int = Field(100 * 1024 * 1024, validation_alias=AliasChoices("MAX_UPLOAD_BYTES"))
    max_zip_files: int = Field(5000, validation_alias=AliasChoices("MAX_ZIP_FILES"))
    max_zip_uncompressed_bytes: int = Field(500 * 1024 * 1024, validation_alias=AliasChoices("MAX_ZIP_UNCOMPRESSED_BYTES"))
    max_file_read_bytes: int = Field(2 * 1024 * 1024, validation_alias=AliasChoices("MAX_FILE_READ_BYTES"))
    max_file_chat_chars: int = Field(24_000, validation_alias=AliasChoices("MAX_FILE_CHAT_CHARS"))
    file_chunk_max_chars: int = Field(4000, validation_alias=AliasChoices("FILE_CHUNK_MAX_CHARS"))
    file_chunk_overlap_chars: int = Field(400, validation_alias=AliasChoices("FILE_CHUNK_OVERLAP_CHARS"))
    file_chunk_max_count: int = Field(500, validation_alias=AliasChoices("FILE_CHUNK_MAX_COUNT"))
    file_retrieval_max_chunks: int = Field(6, validation_alias=AliasChoices("FILE_RETRIEVAL_MAX_CHUNKS"))

    document_extract_max_chars: int = Field(120_000, validation_alias=AliasChoices("DOCUMENT_EXTRACT_MAX_CHARS"))
    document_extract_pdf_max_pages: int = Field(40, validation_alias=AliasChoices("DOCUMENT_EXTRACT_PDF_MAX_PAGES"))
    document_extract_csv_max_rows: int = Field(2000, validation_alias=AliasChoices("DOCUMENT_EXTRACT_CSV_MAX_ROWS"))
    document_extract_xlsx_max_rows_per_sheet: int = Field(500, validation_alias=AliasChoices("DOCUMENT_EXTRACT_XLSX_MAX_ROWS_PER_SHEET"))
    document_extract_xlsx_max_sheets: int = Field(12, validation_alias=AliasChoices("DOCUMENT_EXTRACT_XLSX_MAX_SHEETS"))
    document_extract_docx_max_table_rows: int = Field(2000, validation_alias=AliasChoices("DOCUMENT_EXTRACT_DOCX_MAX_TABLE_ROWS"))
    zip_extract_max_members: int = Field(80, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_MEMBERS"))
    zip_extract_max_member_bytes: int = Field(2 * 1024 * 1024, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_MEMBER_BYTES"))
    zip_extract_max_total_text_chars: int = Field(120_000, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_TOTAL_TEXT_CHARS"))
    zip_extract_max_depth: int = Field(2, validation_alias=AliasChoices("ZIP_EXTRACT_MAX_DEPTH"))
    zip_extract_supported_suffixes: str = Field(".txt,.md,.log,.json,.csv,.html,.htm,.pdf,.docx,.xlsx", validation_alias=AliasChoices("ZIP_EXTRACT_SUPPORTED_SUFFIXES"))

    # v1.8 shared skill pool importer. Bounded for Koyeb Free; skills live in R2 and D1 stores catalogue metadata.
    skill_registry_import_max_items: int = Field(250, validation_alias=AliasChoices("SKILL_REGISTRY_IMPORT_MAX_ITEMS"))
    skill_registry_import_timeout_seconds: int = Field(20, validation_alias=AliasChoices("SKILL_REGISTRY_IMPORT_TIMEOUT_SECONDS"))

    @field_validator("embeddings_model", mode="before")
    @classmethod
    def normalise_cloudflare_embedding_model(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned.startswith("cf/"):
                return f"@{cleaned}"
            return cleaned
        return value

    @field_validator("mast_monitor_mode", mode="before")
    @classmethod
    def normalise_mast_monitor_mode(cls, value: object) -> str:
        cleaned = str(value or "r2").strip().lower()
        return cleaned if cleaned in {"r2", "http", "disabled"} else "r2"

    @field_validator("cors_origins", "allowed_hosts", mode="before")
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
            return self.cf_r2_endpoint_url.rstrip("/")
        if not self.cf_r2_account_id:
            return ""
        return f"https://{self.cf_r2_account_id}.r2.cloudflarestorage.com"


    @property
    def r2_ecosystem_lanes(self) -> list[dict[str, Any]]:
        lanes = [
            ("uploads", self.cf_r2_bucket, self.cf_r2_public_base_url, "Primary HIVE upload/read bucket"),
            ("audits", self.r2_bucket_audits, self.r2_public_base_url_audits, "RAMS/AIMS audit reports"),
            ("art", self.r2_bucket_art, self.r2_public_base_url_art, "Podcast cover art"),
            ("blog", self.r2_bucket_blog, self.r2_public_base_url_blog, "Published blog artefacts"),
            ("blog_images", self.r2_bucket_blog_images, self.r2_public_base_url_blog_images, "Blog imagery"),
            ("blog_rss", self.r2_bucket_blog_rss, self.r2_public_base_url_blog_rss, "Blog RSS feed"),
            ("brand_assets", self.r2_bucket_brand_assets, self.r2_public_base_url_brand_assets, "Shared brand assets"),
            ("meta", self.r2_bucket_meta, self.r2_public_base_url_meta, "Podcast metadata"),
            ("meta_system", self.r2_bucket_meta_system, self.r2_public_base_url_meta_system, "System metadata"),
            ("podcast", self.r2_bucket_podcast, self.r2_public_base_url_podcast, "Podcast audio/pages"),
            ("podcast_rss", self.r2_bucket_podcast_rss_feeds, self.r2_public_base_url_podcast_rss, "Podcast RSS feeds"),
            ("rss", self.r2_bucket_rss_feeds, self.r2_public_base_url_rss, "AI/news RSS feeds"),
            ("transcripts", self.r2_bucket_transcripts, self.r2_public_base_url_transcript, "Podcast transcripts"),
            ("transcript_html", self.r2_bucket_transcripts, self.r2_public_base_url_transcript_html, "Transcript HTML mirror"),
            ("hive_skills", self.r2_bucket_hive_skills, self.r2_public_base_url_hive_skills, "Shared HIVE/AIMS/RAMS skills pool"),
        ]
        payload: list[dict[str, Any]] = []
        write_credentials_configured = bool(
            self.r2_endpoint_url
            and self.cf_r2_access_key_id
            and self.cf_r2_secret_access_key
            and self.cf_r2_bucket
        )
        read_credentials_configured = bool(
            self.r2_multi_bucket_read_enabled
            and self.r2_endpoint_url
            and self.r2_read_access_key_id
            and self.r2_read_secret_access_key
        )
        for name, bucket, public_base_url, description in lanes:
            primary = name == "uploads"
            configured = bool(bucket or public_base_url)
            readable = bool(bucket) and (write_credentials_configured if primary else read_credentials_configured)
            writable = bool(bucket) and primary and write_credentials_configured
            if writable:
                access_mode = "read_write"
            elif readable:
                access_mode = "read_only"
            elif configured:
                access_mode = "registry_only"
            else:
                access_mode = "unavailable"
            payload.append({
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
            })
        return payload

    def r2_lane(self, lane: str) -> dict[str, Any] | None:
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

    @property
    def r2_read_credentials_configured(self) -> bool:
        return bool(
            self.r2_multi_bucket_read_enabled
            and self.r2_endpoint_url
            and self.r2_read_access_key_id
            and self.r2_read_secret_access_key
        )

    def public_url_for_r2_lane(self, lane: str, key: str) -> str | None:
        clean_lane = (lane or "").strip().lower().replace("-", "_")
        clean_key = (key or "").lstrip("/")
        for item in self.r2_ecosystem_lanes:
            if item["lane"] == clean_lane:
                base = item.get("public_base_url")
                return f"{base}/{clean_key}" if base and clean_key else base
        return None

    @property
    def configured_r2_ecosystem_lane_count(self) -> int:
        return sum(1 for item in self.r2_ecosystem_lanes if item.get("configured"))

    @property
    def zip_extract_supported_suffix_set(self) -> set[str]:
        return {item.strip().lower() for item in self.zip_extract_supported_suffixes.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
