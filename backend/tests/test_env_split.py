from pathlib import Path


def test_production_shared_env_contains_no_secret_placeholders() -> None:
    text = Path("HIVE-PRODUCTION-SHARED.env").read_text()
    assert "{{ secret." not in text
    assert "ADMIN_BEARER_TOKEN=" not in text
    assert "OPENROUTER_API_KEY=" not in text
    assert "DATABASE_PASSWORD=" not in text
    assert "R2_SECRET_ACCESS_KEY=" not in text
    assert "RMS_API_KEY=" not in text
    assert "R2_BUCKET_META=" not in text
    # meta_system is a hidden internal control plane for the MAST Worker heartbeat
    # and wake-command queue. It is never exposed through HIVE's user-facing R2 lanes.
    assert "R2_BUCKET_META_SYSTEM=metasystem" in text
    assert "FORWARDED_ALLOW_IPS=127.0.0.1" in text
    assert "FORWARDED_ALLOW_IPS=*" not in text


def test_koyeb_secrets_file_is_secrets_only() -> None:
    lines = [
        line.strip()
        for line in Path("HIVE-KOYEB-SECRETS-ONLY.env").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    keys = {line.split("=", 1)[0] for line in lines}
    assert keys == {
        "ADMIN_BEARER_TOKEN",
        "AIMS_API_KEY",
        "CF_WORKERS_AI_API",
        "D1_API_KEY",
        "D1_DATABASE_ID",
        "DATABASE_PASSWORD",
        "EMBEDDINGS_API_TOKEN",
        "GITHUB_TOKEN",
        "KOYEB_TOKEN",
        "KOYEB_SERVICE_ID_AIMS",
        "KOYEB_SERVICE_ID_RAMS",
        "KOYEB_SERVICE_ID_MAST",
        "KOYEB_SERVICE_ID_HIVE",
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
    assert settings.app_version == "1.31-production"
    assert settings.database_auto_init is True
    assert settings.default_model == "~google/gemini-flash-latest"
    assert settings.r2_bucket_repositories == "hive-repositories"
    assert settings.r2_public_base_url_repositories == ""
    assert settings.r2_public_base_url_audits == ""
    assert settings.r2_public_base_url_hive_skills == ""
    assert settings.ai_search_enabled is True
    assert settings.ai_search_instance == "hive-repositories"
    assert settings.r2_lane("meta") is None
    assert settings.r2_lane("meta_system") is None
    assert "hive.jonathan-harris.online" in settings.effective_allowed_hosts
    assert settings.aims_operational_health_url == "https://zeroth-kara-jonathanharris-3296ed37.koyeb.app/readyz"
    assert settings.aims_ui_health_url == "https://chat.jonathan-harris.online/livez"
    assert settings.aims_ui_readiness_url == "https://chat.jonathan-harris.online/readyz"
    assert settings.mast_monitor_mode == "r2"
    assert settings.repository_github_refresh_enabled is True


def test_start_script_does_not_trust_all_forwarded_headers_by_default() -> None:
    text = Path("scripts/start.sh").read_text()
    assert 'FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-127.0.0.1}"' in text
    assert 'FORWARDED_ALLOW_IPS="${FORWARDED_ALLOW_IPS:-*}"' not in text
