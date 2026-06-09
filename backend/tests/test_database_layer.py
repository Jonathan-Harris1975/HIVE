from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.storage.d1 import D1MetadataStore
from app.storage.sql_store import SqlStore


def test_sqlite_store_initialises_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)

    result = store.init_schema()

    assert result["ok"] is True
    assert store.dialect == "sqlite"
    assert set(store.table_names()).issubset(set(result["tables"]))
    diagnostics = store.diagnostics()
    assert diagnostics["probe"]["ok"] is True


def test_sql_store_records_chat_and_file(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    chat = store.record_chat(
        conversation_id=None,
        mode="general",
        user_message="hello",
        assistant_reply="world",
        model_used="test/model",
        provider="test",
        usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3, "cost": 0},
    )
    assert chat["ok"] is True
    assert chat["conversation_id"]

    file_record = store.record_file(
        {
            "object_key": "uploads/test.txt",
            "original_name": "test.txt",
            "storage": "r2",
            "bucket": "hive",
            "public_url": "https://example.com/test.txt",
            "size_bytes": 12,
            "content_type": "text/plain",
        }
    )
    assert file_record["ok"] is True


def test_database_env_aliases() -> None:
    settings = Settings(
        DATABASE_ENABLED=True,
        DATABASE_HOST="example.pg.koyeb.app",
        DATABASE_USER="koyeb-adm",
        DATABASE_PASSWORD="secret",
        DATABASE_NAME="koyebdb",
    )
    assert settings.sql_database_url.startswith("postgresql://koyeb-adm:secret@example.pg.koyeb.app")
    assert "koyebdb" in settings.sql_database_url


def test_d1_env_aliases() -> None:
    settings = Settings(
        D1_ENABLED=True,
        R2_ACCOUNT_ID="account-123",
        D1_API_KEY="token",
        D1_UUID="database-uuid",
        D1_DATABASE="database-hive",
    )
    store = D1MetadataStore(settings)
    assert store.enabled is True
    assert settings.d1_account_id == "account-123"
    assert settings.d1_database_id == "database-uuid"
    assert settings.d1_database_name == "database-hive"
