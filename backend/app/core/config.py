from functools import lru_cache
from typing import List

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field("JH Ops Chat", validation_alias=AliasChoices("APP_NAME", "OPENROUTER_APP_NAME"))
    app_env: str = "development"
    admin_bearer_token: str = "change-me-local-only"
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173"])

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in {"dev", "development", "local", "test"}


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
    def zip_extract_supported_suffix_set(self) -> set[str]:
        return {item.strip().lower() for item in self.zip_extract_supported_suffixes.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
