from app.core.config import Settings


def test_r2_short_env_aliases_are_supported() -> None:
    settings = Settings(
        R2_ACCOUNT_ID="abc123",
        R2_ACCESS_KEY_ID="access",
        R2_SECRET_ACCESS_KEY="secret",
        R2_BUCKET_UPLOADS="hive",
        R2_PUBLIC_BASE_URL="https://pub.example",
        R2_ENDPOINT_URL="https://abc123.r2.cloudflarestorage.com/",
        R2_REGION="auto",
    )

    assert settings.cf_r2_account_id == "abc123"
    assert settings.cf_r2_access_key_id == "access"
    assert settings.cf_r2_secret_access_key == "secret"
    assert settings.cf_r2_bucket == "hive"
    assert settings.cf_r2_public_base_url == "https://pub.example"
    assert settings.r2_endpoint_url == "https://abc123.r2.cloudflarestorage.com"
    assert settings.r2_region == "auto"


def test_openrouter_app_name_can_set_title_and_app_name() -> None:
    settings = Settings(OPENROUTER_APP_NAME="hive")

    assert settings.app_name == "hive"
    assert settings.openrouter_app_title == "hive"


def test_openrouter_request_timeout_alias_is_supported() -> None:
    settings = Settings(OPENROUTER_REQUEST_TIMEOUT_SECONDS=7)

    assert settings.openrouter_attempt_timeout_seconds == 7


def test_unresolved_koyeb_secret_placeholders_are_treated_as_missing() -> None:
    settings = Settings(
        OPENROUTER_API_KEY='{{ secret.OPENROUTER_HIVE_API_KEY }}',
        RAMS_READINESS_BEARER_TOKEN='{{ secret.RMS_API_KEY }}',
        VECTORIZE_API_TOKEN='{{ secret.Vectorize_API_kEY }}',
    )

    assert settings.openrouter_api_key == ''
    assert settings.rams_readiness_bearer_token == ''
    assert settings.vectorize_api_token == ''


def test_rams_readiness_bearer_token_aliases_are_supported() -> None:
    settings = Settings(RAMS_API_KEY='rams-token')

    assert settings.rams_readiness_bearer_token == 'rams-token'
