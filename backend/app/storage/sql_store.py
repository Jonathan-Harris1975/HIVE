from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote_plus

from app.core.config import Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqlStore:
    """Optional SQL persistence layer for HIVE.

    V1 keeps this deliberately small and dependency-light:
    - SQLite for local/dev smoke tests.
    - Koyeb/PostgreSQL when DATABASE_* env vars are supplied.

    Chat/file endpoints continue to work when this layer is disabled or unavailable.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.url = settings.sql_database_url

    @property
    def enabled(self) -> bool:
        return bool(self.settings.database_enabled and self.url)

    @property
    def dialect(self) -> str:
        if self.url.startswith("postgres://") or self.url.startswith("postgresql://"):
            return "postgres"
        if self.url.startswith("sqlite"):
            return "sqlite"
        return "unknown"

    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "dialect": self.dialect if self.enabled else None,
            "host_configured": bool(self.settings.database_host),
            "user_configured": bool(self.settings.database_user),
            "password_configured": bool(self.settings.database_password),
            "database_name": self.settings.database_name or None,
            "url_configured": bool(self.settings.database_url),
        }

    def init_schema(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "message": "SQL database is disabled or not configured."}

        statements = self._schema_statements()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                for statement in statements:
                    cur.execute(statement)
                conn.commit()
            return {"ok": True, "enabled": True, "dialect": self.dialect, "tables": self.table_names()}
        except Exception as exc:  # pragma: no cover - exact driver exceptions vary by provider
            return {"ok": False, "enabled": True, "dialect": self.dialect, "error": str(exc)}

    def diagnostics(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": True, **self.safe_config()}
        if not self.enabled:
            payload["probe"] = {"ok": False, "message": "SQL database disabled."}
            return payload
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(self._select_one_sql())
                row = cur.fetchone()
            payload["probe"] = {"ok": True, "result": row[0] if row else None}
        except Exception as exc:  # pragma: no cover
            payload["ok"] = False
            payload["probe"] = {"ok": False, "error": str(exc)}
        return payload

    def record_chat(
        self,
        *,
        conversation_id: str | None,
        mode: str,
        user_message: str,
        assistant_reply: str,
        model_used: str | None,
        provider: str | None,
        usage: dict[str, Any] | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}

        conv_id = conversation_id or str(uuid.uuid4())
        total_tokens = _int_or_none((usage or {}).get("total_tokens"))
        cost = _float_or_none((usage or {}).get("cost"))
        created = _now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                self._upsert_conversation(cur, conv_id, mode, model_used, created)
                self._insert_message(cur, conv_id, "user", user_message, None, None, None, None, metadata_json, created)
                self._insert_message(
                    cur,
                    conv_id,
                    "assistant",
                    assistant_reply,
                    model_used,
                    provider,
                    total_tokens,
                    cost,
                    metadata_json,
                    created,
                )
                if usage:
                    self._insert_cost_event(cur, conv_id, model_used, provider, usage, created)
                conn.commit()
            return {"ok": True, "conversation_id": conv_id}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "conversation_id": conv_id, "error": str(exc)}

    def record_file(self, file_result: Any) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}

        data = asdict(file_result) if is_dataclass(file_result) else dict(file_result)
        object_key = data.get("object_key") or data.get("key")
        if not object_key:
            return {"ok": False, "error": "file result has no object_key"}

        now = _now()
        metadata_json = json.dumps(data, ensure_ascii=False, default=str)
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                self._upsert_file(cur, data, metadata_json, now)
                conn.commit()
            return {"ok": True, "object_key": object_key}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "object_key": object_key, "error": str(exc)}

    def table_names(self) -> list[str]:
        return ["hive_conversations", "hive_messages", "hive_files", "hive_cost_events"]

    def _schema_statements(self) -> list[str]:
        # TEXT timestamps keep the schema portable between SQLite and PostgreSQL.
        return [
            """
            CREATE TABLE IF NOT EXISTS hive_conversations (
                id TEXT PRIMARY KEY,
                mode TEXT,
                model TEXT,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS hive_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                provider TEXT,
                token_total INTEGER,
                cost_usd REAL,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hive_messages_conversation
            ON hive_messages (conversation_id, created_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS hive_files (
                id TEXT PRIMARY KEY,
                object_key TEXT UNIQUE NOT NULL,
                filename TEXT,
                storage TEXT,
                bucket TEXT,
                public_url TEXT,
                size_bytes INTEGER,
                content_type TEXT,
                sha256 TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hive_files_object_key
            ON hive_files (object_key)
            """,
            """
            CREATE TABLE IF NOT EXISTS hive_cost_events (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                model TEXT,
                provider TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cost_usd REAL,
                usage_json TEXT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hive_cost_events_created
            ON hive_cost_events (created_at)
            """,
        ]

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self.dialect == "sqlite":
            path = self._sqlite_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path)
            try:
                yield conn
            finally:
                conn.close()
            return

        if self.dialect == "postgres":
            try:
                import psycopg
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("psycopg[binary] is required for PostgreSQL support") from exc
            conn = psycopg.connect(self.url, connect_timeout=self.settings.database_connect_timeout_seconds)
            try:
                yield conn
            finally:
                conn.close()
            return

        raise RuntimeError(f"Unsupported SQL database URL: {self.url!r}")

    def _sqlite_path(self) -> Path:
        value = self.url
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if value.startswith(prefix):
                return Path(value.removeprefix(prefix))
        if value in {"sqlite://", "sqlite:///:memory:", ":memory:"}:
            return Path(":memory:")
        return Path(value)

    def _param(self) -> str:
        return "%s" if self.dialect == "postgres" else "?"

    def _select_one_sql(self) -> str:
        return "SELECT 1"

    def _upsert_conversation(self, cur: Any, conv_id: str, mode: str, model: str | None, now: str) -> None:
        p = self._param()
        try:
            cur.execute(
                f"INSERT INTO hive_conversations (id, mode, model, created_at, updated_at) VALUES ({p}, {p}, {p}, {p}, {p})",
                (conv_id, mode, model, now, now),
            )
        except Exception:
            cur.execute(
                f"UPDATE hive_conversations SET mode={p}, model={p}, updated_at={p} WHERE id={p}",
                (mode, model, now, conv_id),
            )

    def _insert_message(
        self,
        cur: Any,
        conv_id: str,
        role: str,
        content: str,
        model: str | None,
        provider: str | None,
        total_tokens: int | None,
        cost: float | None,
        metadata_json: str,
        created: str,
    ) -> None:
        p = self._param()
        cur.execute(
            f"""
            INSERT INTO hive_messages
            (id, conversation_id, role, content, model, provider, token_total, cost_usd, metadata_json, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
            """,
            (str(uuid.uuid4()), conv_id, role, content, model, provider, total_tokens, cost, metadata_json, created),
        )

    def _upsert_file(self, cur: Any, data: dict[str, Any], metadata_json: str, now: str) -> None:
        p = self._param()
        object_key = data.get("object_key") or data.get("key")
        values = (
            str(uuid.uuid4()),
            object_key,
            data.get("original_name") or data.get("filename") or Path(str(object_key)).name,
            data.get("storage"),
            data.get("bucket"),
            data.get("public_url"),
            _int_or_none(data.get("size_bytes")),
            data.get("content_type"),
            data.get("sha256"),
            metadata_json,
            now,
            now,
        )
        try:
            cur.execute(
                f"""
                INSERT INTO hive_files
                (id, object_key, filename, storage, bucket, public_url, size_bytes, content_type, sha256, metadata_json, created_at, updated_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
                """,
                values,
            )
        except Exception:
            cur.execute(
                f"""
                UPDATE hive_files
                SET filename={p}, storage={p}, bucket={p}, public_url={p}, size_bytes={p}, content_type={p}, sha256={p}, metadata_json={p}, updated_at={p}
                WHERE object_key={p}
                """,
                (
                    values[2],
                    values[3],
                    values[4],
                    values[5],
                    values[6],
                    values[7],
                    values[8],
                    values[9],
                    now,
                    object_key,
                ),
            )

    def _insert_cost_event(
        self,
        cur: Any,
        conv_id: str,
        model: str | None,
        provider: str | None,
        usage: dict[str, Any],
        created: str,
    ) -> None:
        p = self._param()
        cur.execute(
            f"""
            INSERT INTO hive_cost_events
            (id, conversation_id, model, provider, prompt_tokens, completion_tokens, total_tokens, cost_usd, usage_json, created_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
            """,
            (
                str(uuid.uuid4()),
                conv_id,
                model,
                provider,
                _int_or_none(usage.get("prompt_tokens")),
                _int_or_none(usage.get("completion_tokens")),
                _int_or_none(usage.get("total_tokens")),
                _float_or_none(usage.get("cost")),
                json.dumps(usage, ensure_ascii=False, default=str),
                created,
            ),
        )


class DatabaseUrlBuilder:
    @staticmethod
    def from_parts(settings: Settings) -> str:
        if settings.database_url:
            return settings.database_url
        if settings.database_host and settings.database_user and settings.database_name:
            user = quote_plus(settings.database_user)
            password = quote_plus(settings.database_password)
            auth = f"{user}:{password}" if password else user
            port = f":{settings.database_port}" if settings.database_port else ""
            ssl = f"?sslmode={quote_plus(settings.database_sslmode)}" if settings.database_sslmode else ""
            return f"postgresql://{auth}@{settings.database_host}{port}/{settings.database_name}{ssl}"
        if settings.is_dev:
            return "sqlite:///./local-data/hive.sqlite3"
        return ""


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
