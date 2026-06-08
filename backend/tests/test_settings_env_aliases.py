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
