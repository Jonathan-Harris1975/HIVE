from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

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


def test_d1_operational_backend_requires_its_own_database_id() -> None:
    settings = Settings(
        DATABASE_ENABLED=True,
        DATABASE_BACKEND="d1",
        DATABASE_D1_DATABASE_ID="operational-id",
        D1_ACCOUNT_ID="account-123",
        D1_API_KEY="token",
    )
    store = SqlStore(settings)
    assert store.enabled is True
    assert store.dialect == "d1"
    assert store.safe_config()["d1_database_id_configured"] is True


def test_d1_cursor_reads_ctes_and_settles_writes_drained_by_another_cursor(monkeypatch) -> None:
    from app.storage.d1_sql_client import D1Connection, _is_read_statement

    assert _is_read_statement("WITH recent AS (SELECT 1) SELECT * FROM recent") is True
    assert _is_read_statement("WITH recent AS (SELECT 1) DELETE FROM recent") is False
    connection = D1Connection(account_id="account", database_id="db", api_key="token")
    writer = connection.cursor()
    reader = connection.cursor()

    def fake_request(payload):
        statements = payload.get("batch", [payload])
        return [
            {
                "success": True,
                "results": [{"value": 1}] if statement["sql"].startswith("SELECT") else [],
                "meta": {"changes": index + 4},
            }
            for index, statement in enumerate(statements)
        ]

    monkeypatch.setattr(connection, "_request", fake_request)
    writer.execute("UPDATE hive_conversations SET title=? WHERE id=?", ("x", "one"))
    reader.execute("SELECT 1 AS value")

    assert writer.rowcount == 4
    assert reader.description[0][0] == "value"


def test_d1_store_commits_chat_writes_as_one_batch(monkeypatch) -> None:
    from app.storage import d1_sql_client

    calls: list[dict[str, object]] = []

    class FakeResponse:
        status_code = 200
        content = b"{}"

        def __init__(self, payload: dict[str, object]) -> None:
            self.payload = payload

        def json(self) -> dict[str, object]:
            return self.payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def post(self, _url, *, headers, json):
            calls.append({"headers": headers, "json": json})
            statements = json.get("batch", [json])
            return FakeResponse({
                "success": True,
                "result": [
                    {"success": True, "results": [], "meta": {"changes": 1}}
                    for _ in statements
                ],
            })

    monkeypatch.setattr(d1_sql_client.httpx, "Client", FakeClient)
    store = SqlStore(Settings(
        DATABASE_ENABLED=True,
        DATABASE_BACKEND="d1",
        DATABASE_D1_DATABASE_ID="operational-id",
        D1_ACCOUNT_ID="account-123",
        D1_API_KEY="token",
    ))

    result = store.record_chat(
        conversation_id="conv-test",
        mode="general",
        user_message="hello",
        assistant_reply="world",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 2, "cost": 0.01},
    )

    assert result["ok"] is True
    assert len(calls) == 1
    batch = calls[0]["json"]["batch"]
    assert len(batch) == 4
    assert "hive_conversations" in batch[0]["sql"]
    assert all("__PARAM__" not in statement["sql"] for statement in batch)


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
    assert conversations["conversations"][0]["total_tokens"] == 15
    assert conversations["conversations"][0]["total_cost_usd"] == 0.001

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


def test_sql_store_record_chat_auto_initialises_missing_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "auto-init.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
        OPENROUTER_API_KEY="test",
    )
    store = SqlStore(settings)

    result = store.record_chat(
        conversation_id="auto-init-conv",
        mode="general",
        user_message="Persist without manual schema init",
        assistant_reply="Saved after schema creation",
        model_used="test/model",
        provider="test-provider",
        usage={"total_tokens": 4, "cost": 0},
    )

    assert result["ok"] is True
    assert result["schema_auto_init"]["ok"] is True
    saved = store.get_conversation("auto-init-conv")
    assert saved["ok"] is True
    assert saved["message_count"] == 2


def test_sql_store_reset_all_data_preserves_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "hive.sqlite3"
    settings = Settings(
        APP_ENV="test",
        DATABASE_ENABLED=True,
        DATABASE_URL=f"sqlite:///{db_path}",
    )
    store = SqlStore(settings)
    assert store.init_schema()["ok"] is True
    assert store.record_chat(
        conversation_id="reset-conversation",
        mode="general",
        user_message="before reset",
        assistant_reply="persisted",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 2, "cost": 0},
    )["ok"] is True
    assert store.record_file(
        {
            "object_key": "uploads/reset.txt",
            "original_name": "reset.txt",
            "storage": "r2",
            "bucket": "hive",
            "size_bytes": 5,
            "content_type": "text/plain",
        }
    )["ok"] is True

    result = store.reset_all_data()

    assert result["ok"] is True
    assert all(value == 0 for value in result["after"].values())
    assert set(store.table_names()) == set(store.table_counts()["counts"])
    assert store.record_chat(
        conversation_id="after-reset",
        mode="general",
        user_message="schema still works",
        assistant_reply="yes",
        model_used="test/model",
        provider="test",
        usage={"total_tokens": 1, "cost": 0},
    )["ok"] is True


def test_d1_reset_database_names_parse_from_env() -> None:
    settings = Settings(D1_RESET_DATABASE_NAMES="database-hive,database-comms-hub")
    assert settings.d1_reset_database_names == ["database-hive", "database-comms-hub"]


def test_d1_purge_preserves_schema_migration_ledger(monkeypatch) -> None:
    settings = Settings(
        D1_ENABLED=True,
        D1_ACCOUNT_ID="account",
        D1_API_KEY="token",
        D1_DATABASE_ID="hive-id",
        D1_RESET_DATABASE_NAMES="database-comms-hub",
    )
    store = D1MetadataStore(settings)
    queries: list[str] = []

    def fake_query(database_id: str, sql: str, params=None):
        queries.append(sql)
        if "sqlite_master" in sql:
            return {
                "ok": True,
                "result": [{"results": [
                    {"name": "comms_hub_contacts"},
                    {"name": "comms_hub_messages"},
                    {"name": "comms_hub_schema_migrations"},
                ]}],
            }
        return {"ok": True, "result": []}

    monkeypatch.setattr(store, "_query_database", fake_query)

    result = store._purge_database(database_id="comms-id", database_name="database-comms-hub")

    assert result["ok"] is True
    assert result["preserved_tables"] == ["comms_hub_schema_migrations"]
    purge_sql = queries[-1]
    assert 'DELETE FROM "comms_hub_contacts"' in purge_sql
    assert 'DELETE FROM "comms_hub_messages"' in purge_sql
    assert 'DELETE FROM "comms_hub_schema_migrations"' not in purge_sql
    assert "PRAGMA defer_foreign_keys = ON" in purge_sql


def test_purge_reset_endpoint_requires_target_specific_confirmation() -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = Settings(APP_ENV="test", DATABASE_ENABLED=False, D1_ENABLED=False)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/db/purge-reset",
            json={"target": "hive_sql", "confirmation": "RESET ALL"},
        )
    assert response.status_code == 400


def test_purge_reset_endpoint_resets_only_selected_sql_target(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    calls: list[str] = []
    monkeypatch.setattr(
        SqlStore,
        "reset_all_data",
        lambda self: calls.append("sql") or {"ok": True, "tables_cleared": ["hive_conversations"]},
    )
    monkeypatch.setattr(
        D1MetadataStore,
        "reset_configured_databases",
        lambda self, names=None: calls.append("d1") or {"ok": True},
    )
    with TestClient(create_app(Settings(APP_ENV="test", DATABASE_ENABLED=False, D1_ENABLED=False))) as client:
        response = client.post(
            "/v1/db/purge-reset",
            json={"target": "hive_sql", "confirmation": "RESET HIVE SQL"},
        )
    assert response.status_code == 200
    assert response.json()["target"] == "hive_sql"
    assert calls == ["sql"]


def test_purge_reset_preview_is_read_only(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    calls: list[str] = []
    monkeypatch.setattr(
        SqlStore,
        "preview_reset",
        lambda self: calls.append("preview") or {
            "ok": True, "target": "hive_sql", "tables": [{"name": "hive_messages", "rows": 3}], "total_rows": 3,
        },
    )
    with TestClient(create_app(Settings(APP_ENV="test", DATABASE_ENABLED=False, D1_ENABLED=False))) as client:
        response = client.post("/v1/db/purge-reset/preview", json={"target": "hive_sql"})
    assert response.status_code == 200
    assert response.json()["total_rows"] == 3
    assert calls == ["preview"]


def test_conversation_cost_includes_title_and_extra_attempts_and_unknowns(tmp_path: Path) -> None:
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True, DATABASE_URL=f"sqlite:///{tmp_path / 'costs.sqlite'}")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]
    assert store.record_chat(conversation_id="costs", mode="general", user_message="hi", assistant_reply="hello",
                             model_used="winner", provider="p", usage={"cost": 0.02, "total_tokens": 4},
                             extra_usage=[{"model": "first", "provider": "p1", "usage": {"cost": 0.01}}])["ok"]
    assert store.record_usage_event(conversation_id="costs", model_used="title", provider="p2", usage={"cost": 0.03})["ok"]
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] == pytest.approx(0.06)
    assert store.record_usage_event(conversation_id="costs", model_used="unknown", provider=None, usage={})["ok"]
    assert store.list_conversations()["conversations"][0]["total_cost_usd"] is None


def test_extra_attempt_write_failure_rolls_back_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True, DATABASE_URL=f"sqlite:///{tmp_path / 'rollback.sqlite'}")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]
    original = store._insert_cost_event
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("attempt write failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "_insert_cost_event", fail_second)
    result = store.record_chat(conversation_id="rollback", mode="general", user_message="hi", assistant_reply="hello",
                               model_used="winner", provider="p", usage={"cost": 0.02},
                               extra_usage=[{"model": "first", "usage": {"cost": 0.01}}])
    assert result["ok"] is False
    assert "attempt write failed" in result["error"]
    assert store.list_conversations()["count"] == 0


def test_chat_history_preserves_turn_order_for_equal_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    timestamp = "2026-01-01T00:00:00+00:00"
    monkeypatch.setattr("app.storage.sql_store._now", lambda: timestamp)
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True, DATABASE_URL=f"sqlite:///{tmp_path / 'order.sqlite'}")
    store = SqlStore(settings)
    assert store.init_schema()["ok"]
    for user_message, assistant_reply in (("question one", "answer one"), ("question two", "answer two")):
        assert store.record_chat(
            conversation_id="order",
            mode="general",
            user_message=user_message,
            assistant_reply=assistant_reply,
            model_used="test",
            provider="test",
            usage={"cost": 0},
        )["ok"]

    expected = [
        ("user", "question one"),
        ("assistant", "answer one"),
        ("user", "question two"),
        ("assistant", "answer two"),
    ]
    conversation = store.get_conversation("order")
    assert [(item["role"], item["content"]) for item in conversation["messages"]] == expected
    assert [(item["role"], item["content"]) for item in store.recent_chat_turns("order")] == expected
    assert store.first_conversation_exchange("order") == {
        "user_message": "question one",
        "assistant_reply": "answer one",
    }


@pytest.mark.parametrize(
    "sequence_columns_present",
    [False, True],
    ids=["new-columns", "existing-zero-columns"],
)
def test_schema_migration_backfills_legacy_message_sequences(
    tmp_path: Path, sequence_columns_present: bool
) -> None:
    db_path = tmp_path / "legacy-order.sqlite"
    settings = Settings(APP_ENV="test", DATABASE_ENABLED=True, DATABASE_URL=f"sqlite:///{db_path}")
    store = SqlStore(settings)
    turn_sequence_column = (
        "turn_sequence INTEGER NOT NULL DEFAULT 0, " if sequence_columns_present else ""
    )
    message_sequence_column = (
        "message_sequence INTEGER NOT NULL DEFAULT 0, " if sequence_columns_present else ""
    )
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"""CREATE TABLE hive_conversations (
                id TEXT PRIMARY KEY, mode TEXT NOT NULL, model TEXT, title TEXT,
                auto_titled INTEGER NOT NULL DEFAULT 0, {turn_sequence_column}
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            f"""CREATE TABLE hive_messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, {message_sequence_column}
                model TEXT, provider TEXT, token_total INTEGER,
                cost_usd REAL, metadata_json TEXT, created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO hive_conversations (id, mode, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("legacy", "general", "2026-01-01", "2026-01-01"),
        )
        for message_id, role, content in (
            ("msg-d", "assistant", "second answer"),
            ("msg-c", "user", "second question"),
            ("msg-b", "assistant", "first answer"),
            ("msg-a", "user", "first question"),
        ):
            conn.execute(
                "INSERT INTO hive_messages "
                "(id, conversation_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, "legacy", role, content, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    assert store.init_schema()["ok"] is True
    with store._transaction() as cur:
        cur.execute(
            "SELECT id, message_sequence FROM hive_messages WHERE conversation_id=? "
            "ORDER BY message_sequence",
            ("legacy",),
        )
        messages = cur.fetchall()
        cur.execute("SELECT turn_sequence FROM hive_conversations WHERE id=?", ("legacy",))
        turn_sequence = cur.fetchone()[0]
    assert messages == [("msg-a", 1), ("msg-b", 2), ("msg-c", 3), ("msg-d", 4)]
    assert turn_sequence == 2

    assert store.record_chat(
        conversation_id="legacy",
        mode="general",
        user_message="third question",
        assistant_reply="third answer",
        model_used="test",
        provider="test",
        usage={"cost": 0},
    )["ok"]
    with store._transaction() as cur:
        cur.execute(
            "SELECT message_sequence FROM hive_messages WHERE conversation_id=? ORDER BY message_sequence",
            ("legacy",),
        )
        assert [row[0] for row in cur.fetchall()] == [1, 2, 3, 4, 5, 6]
