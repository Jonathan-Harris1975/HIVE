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

    cf_r2_account_id: str = Field("", validation_alias=AliasChoices("CF_R2_ACCOUNT_ID", "R2_ACCOUNT_ID"))
    cf_r2_access_key_id: str = Field("", validation_alias=AliasChoices("CF_R2_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID"))
    cf_r2_secret_access_key: str = Field("", validation_alias=AliasChoices("CF_R2_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY"))
    cf_r2_bucket: str = Field("ops-chat-uploads", validation_alias=AliasChoices("CF_R2_BUCKET", "R2_BUCKET_UPLOADS", "R2_BUCKET"))
    cf_r2_public_base_url: str = Field("", validation_alias=AliasChoices("CF_R2_PUBLIC_BASE_URL", "R2_PUBLIC_BASE_URL"))
    cf_r2_endpoint_url: str = Field("", validation_alias=AliasChoices("CF_R2_ENDPOINT_URL", "R2_ENDPOINT_URL"))
    r2_region: str = Field("auto", validation_alias=AliasChoices("R2_REGION", "AWS_REGION"))

    database_url: str = "sqlite+aiosqlite:///./local-data/jh_ops_chat.sqlite3"

    cf_account_id: str = Field("", validation_alias=AliasChoices("CF_ACCOUNT_ID", "R2_ACCOUNT_ID"))
    cf_api_token: str = Field("", validation_alias=AliasChoices("CF_API_TOKEN", "VECTORIZE_API_TOKEN"))
    cf_vectorize_index: str = Field("jh-ops-chat", validation_alias=AliasChoices("CF_VECTORIZE_INDEX", "VECTORIZE_INDEX_NAME"))
    cf_embedding_model: str = "@cf/baai/bge-base-en-v1.5"

    max_upload_bytes: int = 100 * 1024 * 1024
    max_zip_files: int = 5000
    max_zip_uncompressed_bytes: int = 500 * 1024 * 1024
    max_file_read_bytes: int = 2 * 1024 * 1024
    max_file_chat_chars: int = 24_000

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
    def r2_endpoint_url(self) -> str:
        if self.cf_r2_endpoint_url:
            return self.cf_r2_endpoint_url.rstrip("/")
        if not self.cf_r2_account_id:
            return ""
        return f"https://{self.cf_r2_account_id}.r2.cloudflarestorage.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
