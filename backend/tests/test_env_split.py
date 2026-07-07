from pathlib import Path


def test_production_shared_env_contains_no_secret_placeholders() -> None:
    text = Path("HIVE-PRODUCTION-SHARED.env").read_text()
    assert "{{ secret." not in text
    assert "ADMIN_BEARER_TOKEN=" not in text
    assert "OPENROUTER_API_KEY=" not in text
    assert "DATABASE_PASSWORD=" not in text
    assert "R2_SECRET_ACCESS_KEY=" not in text
    assert "RMS_API_KEY=" not in text


def test_koyeb_secrets_file_is_secrets_only() -> None:
    lines = [
        line.strip()
        for line in Path("HIVE-KOYEB-SECRETS-ONLY.env").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    keys = {line.split("=", 1)[0] for line in lines}
    assert keys == {
        "ADMIN_BEARER_TOKEN",
        "D1_API_KEY",
        "D1_DATABASE_ID",
        "DATABASE_PASSWORD",
        "EMBEDDINGS_API_TOKEN",
        "OPENROUTER_API_KEY",
        "OPS_EVENT_INGEST_TOKEN",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_READ_ACCESS_KEY_ID",
        "R2_READ_SECRET_ACCESS_KEY",
        "RMS_API_KEY",
        "VECTORIZE_API_TOKEN",
    }
    assert all("{{ secret." in line for line in lines)


def test_settings_loads_repo_shared_env_file() -> None:
    from app.core.config import Settings

    settings = Settings(_env_file="HIVE-PRODUCTION-SHARED.env")
    assert settings.app_version == "1.30-production"
    assert settings.database_auto_init is True
    assert settings.default_model == "~google/gemini-flash-latest"
    assert "hive.jonathan-harris.online" in settings.effective_allowed_hosts
