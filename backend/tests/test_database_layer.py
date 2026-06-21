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


def test_sql_store_lists_conversations_files_and_costs(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    chat = store.record_chat(
        conversation_id="conv-1",
        mode="general",
        user_message="hello",
        assistant_reply="world",
        model_used="test/model",
        provider="test-provider",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.001},
    )
    assert chat["ok"] is True
    assert store.record_file(
        {
            "object_key": "uploads/example.txt",
            "original_name": "example.txt",
            "storage": "r2",
            "bucket": "hive",
            "public_url": "https://example.com/example.txt",
            "size_bytes": 7,
            "content_type": "text/plain",
        }
    )["ok"] is True

    conversations = store.list_conversations(limit=10)
    assert conversations["ok"] is True
    assert conversations["count"] == 1
    assert conversations["conversations"][0]["id"] == "conv-1"

    conversation = store.get_conversation("conv-1")
    assert conversation["ok"] is True
    assert conversation["message_count"] == 2
    assert [item["role"] for item in conversation["messages"]] == ["user", "assistant"]

    turns = store.recent_chat_turns("conv-1", limit=10)
    assert turns == [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}]

    files = store.list_files(limit=10)
    assert files["ok"] is True
    assert files["files"][0]["object_key"] == "uploads/example.txt"

    costs = store.cost_summary()
    assert costs["ok"] is True
    assert costs["totals"]["total_tokens"] == 15
    assert costs["by_model"][0]["model"] == "test/model"

    counts = store.table_counts()
    assert counts["ok"] is True
    assert counts["counts"]["hive_conversations"] == 1


def test_chat_payload_hydrates_persisted_history(tmp_path: Path) -> None:
    from app.api.chat import ChatRequest, build_payload

    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    assert store.record_chat(
        conversation_id="conv-history",
        mode="general",
        user_message="Remember the colour is blue.",
        assistant_reply="I will remember blue.",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 3, "cost": 0},
    )["ok"] is True

    payload, _fallbacks = build_payload(
        ChatRequest(
            conversation_id="conv-history",
            message="What colour did I mention?",
            mode="general",
            model="test/model",
            db_history_limit=10,
        ),
        settings,
    )

    contents = [message["content"] for message in payload["messages"]]
    assert "Remember the colour is blue." in contents
    assert "I will remember blue." in contents
    assert contents[-1] == "What colour did I mention?"


def test_sql_store_reuses_existing_conversation_without_poisoning_transaction(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    first = store.record_chat(
        conversation_id="same-conversation",
        mode="general",
        user_message="first",
        assistant_reply="reply one",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 1, "cost": 0},
    )
    second = store.record_chat(
        conversation_id="same-conversation",
        mode="general",
        user_message="second",
        assistant_reply="reply two",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 2, "cost": 0},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    conversation = store.get_conversation("same-conversation", limit=10)
    assert conversation["ok"] is True
    assert conversation["message_count"] == 4


def test_sql_store_file_upsert_rewrites_without_transaction_poison(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    first = store.record_file(
        {
            "object_key": "uploads/duplicate.txt",
            "original_name": "duplicate.txt",
            "storage": "r2",
            "bucket": "hive",
            "size_bytes": 1,
            "content_type": "text/plain",
        }
    )
    second = store.record_file(
        {
            "object_key": "uploads/duplicate.txt",
            "original_name": "duplicate.txt",
            "storage": "r2",
            "bucket": "hive",
            "size_bytes": 2,
            "content_type": "text/plain",
        }
    )

    assert first["ok"] is True
    assert second["ok"] is True
    files = store.list_files(limit=10)
    assert files["ok"] is True
    assert files["count"] == 1
    assert files["files"][0]["size_bytes"] == 2


def test_sql_store_ping_write_is_ephemeral(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    probe = store.ping_write()

    assert probe["ok"] is True
    conversations = store.list_conversations(limit=10)
    assert conversations["ok"] is True
    assert conversations["count"] == 0


def test_postgres_statement_timeout_sql_uses_safe_literal() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL="postgresql://user:pass@example.com:5432/db?sslmode=require",
        DATABASE_STATEMENT_TIMEOUT_SECONDS=30,
    )
    store = SqlStore(settings)

    timeout_sql = store._postgres_statement_timeout_sql()

    assert timeout_sql == "SET statement_timeout = 30000"
    assert "%s" not in timeout_sql
    assert "$1" not in timeout_sql


def test_postgres_statement_timeout_can_be_disabled() -> None:
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL="postgresql://user:pass@example.com:5432/db?sslmode=require",
        DATABASE_STATEMENT_TIMEOUT_SECONDS=0,
    )
    store = SqlStore(settings)

    assert store._postgres_statement_timeout_sql() is None


def test_sql_store_records_lists_and_searches_file_chunks(tmp_path: Path) -> None:
    from app.ingestion.chunking import chunks_to_dicts, split_text_into_chunks

    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    chunks = split_text_into_chunks(
        "Alpha badger context.\n\nBeta fox context.\n\nGamma badger retrieval target.",
        max_chars=80,
        overlap_chars=10,
    )
    result = store.record_file_chunks(
        object_key="uploads/chunked.txt",
        chunks=chunks_to_dicts(chunks),
        source_metadata={"content_type": "text/plain"},
    )

    assert result["ok"] is True
    assert result["chunk_count"] >= 1
    listed = store.list_file_chunks(object_key="uploads/chunked.txt", include_content=True)
    assert listed["ok"] is True
    assert listed["count"] == result["chunk_count"]
    found = store.search_file_chunks(query="badger retrieval", object_key="uploads/chunked.txt", limit=2)
    assert found["ok"] is True
    assert found["count"] >= 1
    assert "badger" in found["chunks"][0]["content"].lower()
    counts = store.table_counts()
    assert counts["counts"]["hive_file_chunks"] == result["chunk_count"]


def test_sql_store_cleanup_test_records_dry_run_and_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    assert store.record_chat(
        conversation_id="cleanup-conv",
        mode="general",
        user_message="cleanup user",
        assistant_reply="cleanup reply",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 4, "cost": 0},
        metadata={"test_run_id": "run-clean"},
    )["ok"] is True
    assert store.record_file(
        {
            "object_key": "uploads/run-clean/file.txt",
            "original_name": "file.txt",
            "storage": "r2",
            "bucket": "hive",
            "size_bytes": 4,
            "content_type": "text/plain",
        },
        extra_metadata={"test_run_id": "run-clean"},
    )["ok"] is True
    assert store.record_file_chunks(
        object_key="uploads/run-clean/file.txt",
        chunks=[{
            "chunk_index": 0,
            "content": "cleanup chunk",
            "char_start": 0,
            "char_end": 13,
            "token_estimate": 3,
            "content_sha256": "sha-clean",
        }],
        source_metadata={"test_run_id": "run-clean"},
    )["ok"] is True

    dry = store.cleanup_test_records(test_run_id="run-clean", dry_run=True)
    assert dry["ok"] is True
    assert dry["dry_run"] is True
    assert dry["matched"]["conversations"] == 1
    assert dry["matched"]["files"] == 1

    deleted = store.cleanup_test_records(test_run_id="run-clean", dry_run=False)
    assert deleted["ok"] is True
    assert deleted["deleted"]["conversations"] == 1
    assert store.list_conversations(limit=10)["count"] == 0
    assert store.list_files(limit=10)["count"] == 0
    assert store.list_file_chunks(object_key="uploads/run-clean/file.txt")["count"] == 0


def test_conversation_titles_rename_and_delete(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    recorded = store.record_chat(
        conversation_id="conv-manage",
        mode="general",
        user_message="This is the automatically generated conversation title",
        assistant_reply="Recorded.",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 2, "cost": 0.001},
    )
    assert recorded["ok"] is True

    before = store.get_conversation("conv-manage")
    assert before["conversation"]["title"] == "This is the automatically generated conversation title"

    renamed = store.rename_conversation("conv-manage", "Renamed conversation")
    assert renamed == {
        "ok": True,
        "enabled": True,
        "conversation_id": "conv-manage",
        "title": "Renamed conversation",
    }
    after = store.get_conversation("conv-manage")
    assert after["conversation"]["title"] == "Renamed conversation"

    deleted = store.delete_conversation("conv-manage")
    assert deleted["ok"] is True
    assert deleted["conversations_deleted"] == 1
    assert deleted["messages_deleted"] == 2
    assert deleted["cost_events_deleted"] == 1
    assert store.get_conversation("conv-manage")["error"] == "conversation_not_found"


def test_sql_store_strips_nul_bytes_before_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True

    chat = store.record_chat(
        conversation_id="conv-nul\x00id",
        mode="general\x00mode",
        user_message="hello\x00there",
        assistant_reply="world\x00reply",
        model_used="model\x00id",
        provider="provider\x00id",
        usage={"total_tokens": 1, "bad": "value\x00inside"},
        metadata={"source\x00key": {"nested": "bad\x00value"}},
    )

    assert chat["ok"] is True
    conversation = store.get_conversation("conv-nul�id")
    assert conversation["ok"] is True
    serialised = str(conversation)
    assert "\x00" not in serialised
    assert "�" in serialised
