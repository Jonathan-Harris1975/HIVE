from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "JH Ops Chat"
    app_env: str = "development"
    admin_bearer_token: str = "change-me-local-only"
    cors_origins: List[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://jonathan-harris.online"
    openrouter_app_title: str = "JH Ops Chat"

    # Model policy is deliberately env-driven. Defaults use currently common
    # OpenRouter aliases / cheap high-context models rather than brittle dated IDs.
    default_model: str = "~openai/gpt-mini-latest"
    cheap_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    balanced_model: str = "~google/gemini-flash-latest"
    premium_model: str = "~anthropic/claude-sonnet-latest"
    code_model: str = "x-ai/grok-build-0.1"
    audit_model: str = "~anthropic/claude-sonnet-latest"
    openrouter_free_fallback_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"

    cf_r2_account_id: str = ""
    cf_r2_access_key_id: str = ""
    cf_r2_secret_access_key: str = ""
    cf_r2_bucket: str = "ops-chat-uploads"
    cf_r2_public_base_url: str = ""

    database_url: str = "sqlite+aiosqlite:///./local-data/jh_ops_chat.sqlite3"

    cf_account_id: str = ""
    cf_api_token: str = ""
    cf_vectorize_index: str = "jh-ops-chat"
    cf_embedding_model: str = "@cf/baai/bge-base-en-v1.5"

    max_upload_bytes: int = 100 * 1024 * 1024
    max_zip_files: int = 5000
    max_zip_uncompressed_bytes: int = 500 * 1024 * 1024

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
        if not self.cf_r2_account_id:
            return ""
        return f"https://{self.cf_r2_account_id}.r2.cloudflarestorage.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()
