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

    Production rules:
    - Works with SQLite for local/dev smoke tests.
    - Works with Koyeb/PostgreSQL for production persistence.
    - Uses true UPSERTs instead of insert-then-update exception flow.
    - Rolls back every failed transaction before closing.
    - Returns structured diagnostics; chat/file routes keep working if DB writes fail.
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
            "sslmode": self.settings.database_sslmode or None,
            "connect_timeout_seconds": self.settings.database_connect_timeout_seconds,
        }

    def init_schema(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "message": "SQL database is disabled or not configured."}

        statements = self._schema_statements()
        try:
            with self._transaction() as cur:
                for statement in statements:
                    cur.execute(statement)
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

    def ping_write(self) -> dict[str, object]:
        """Verify that the SQL write path can commit and roll back cleanly.

        This creates and deletes a tiny probe conversation in one transaction. It is
        safe to run repeatedly and should be the first check after a DB error.
        """

        if not self.enabled:
            return {"ok": False, "enabled": False}

        probe_id = f"probe-{uuid.uuid4()}"
        now = _now()
        p = self._param()
        try:
            with self._transaction() as cur:
                cur.execute(
                    f"INSERT INTO hive_conversations (id, mode, model, title, created_at, updated_at) VALUES ({p}, {p}, {p}, {p}, {p}, {p})",
                    (probe_id, "diagnostic", "probe", "SQL write probe", now, now),
                )
                cur.execute(f"DELETE FROM hive_conversations WHERE id={p}", (probe_id,))
            return {"ok": True, "enabled": True, "dialect": self.dialect, "probe_id": probe_id}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "enabled": True, "dialect": self.dialect, "probe_id": probe_id, "error": str(exc)}

    def record_chat(
        self,
        *,
        conversation_id: str | None,
        mode: str,
        user_message: str,
        assistant_reply: str | None,
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
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)
        safe_reply = assistant_reply or ""

        try:
            with self._transaction() as cur:
                self._upsert_conversation(cur, conv_id, mode, model_used, created)
                self._insert_message(cur, conv_id, "user", user_message or "", None, None, None, None, metadata_json, created)
                self._insert_message(
                    cur,
                    conv_id,
                    "assistant",
                    safe_reply,
                    model_used,
                    provider,
                    total_tokens,
                    cost,
                    metadata_json,
                    created,
                )
                if usage:
                    self._insert_cost_event(cur, conv_id, model_used, provider, usage, created)
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
            with self._transaction() as cur:
                self._upsert_file(cur, data, metadata_json, now)
            return {"ok": True, "object_key": object_key}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "object_key": object_key, "error": str(exc)}

    def table_counts(self) -> dict[str, object]:
        """Return safe row counts for operational tables.

        Each table count uses a separate connection so one missing table does not
        poison the rest of the diagnostic check on PostgreSQL.
        """

        if not self.enabled:
            return {"ok": False, "enabled": False}
        counts: dict[str, int | str] = {}
        for table in self.table_names():
            try:
                with self._connect() as conn:
                    cur = conn.cursor()
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    row = cur.fetchone()
                    counts[table] = int(row[0]) if row else 0
            except Exception as exc:  # table may not exist before /db/init
                counts[table] = f"unavailable: {exc}"
        return {"ok": True, "enabled": True, "counts": counts}

    def list_conversations(self, *, limit: int = 50) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        p = self._param()
        sql = f"""
            SELECT
              c.id,
              c.mode,
              c.model,
              c.title,
              c.created_at,
              c.updated_at,
              COUNT(m.id) AS message_count,
              COALESCE(SUM(CASE WHEN m.role='assistant' THEN m.cost_usd ELSE 0 END), 0) AS cost_usd
            FROM hive_conversations c
            LEFT JOIN hive_messages m ON m.conversation_id = c.id
            GROUP BY c.id, c.mode, c.model, c.title, c.created_at, c.updated_at
            ORDER BY c.updated_at DESC
            LIMIT {p}
        """
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(sql, (_int_or_none(limit) or 50,))
                rows = self._fetch_dicts(cur)
            return {"ok": True, "enabled": True, "count": len(rows), "conversations": rows}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "enabled": True, "error": str(exc)}

    def get_conversation(self, conversation_id: str, *, limit: int = 100) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        p = self._param()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT id, mode, model, title, created_at, updated_at FROM hive_conversations WHERE id={p}",
                    (conversation_id,),
                )
                conversation_rows = self._fetch_dicts(cur)
                if not conversation_rows:
                    return {"ok": False, "enabled": True, "error": "conversation_not_found", "conversation_id": conversation_id}
                cur.execute(
                    f"""
                    SELECT id, role, content, model, provider, token_total, cost_usd, metadata_json, created_at
                    FROM hive_messages
                    WHERE conversation_id={p}
                    ORDER BY created_at DESC
                    LIMIT {p}
                    """,
                    (conversation_id, _int_or_none(limit) or 100),
                )
                messages = list(reversed(self._fetch_dicts(cur)))
            for message in messages:
                message["metadata"] = _json_or_none(message.pop("metadata_json", None))
            return {
                "ok": True,
                "enabled": True,
                "conversation": conversation_rows[0],
                "message_count": len(messages),
                "messages": messages,
            }
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "enabled": True, "conversation_id": conversation_id, "error": str(exc)}

    def recent_chat_turns(self, conversation_id: str, *, limit: int = 20) -> list[dict[str, str]]:
        """Return recent user/assistant turns suitable for model context hydration."""

        if not self.enabled or not conversation_id:
            return []
        p = self._param()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    SELECT role, content
                    FROM hive_messages
                    WHERE conversation_id={p} AND role IN ('user', 'assistant')
                    ORDER BY created_at DESC
                    LIMIT {p}
                    """,
                    (conversation_id, _int_or_none(limit) or 20),
                )
                rows = list(reversed(self._fetch_dicts(cur)))
            return [
                {"role": str(row.get("role")), "content": str(row.get("content") or "")}
                for row in rows
                if row.get("role") in {"user", "assistant"} and row.get("content")
            ]
        except Exception:
            return []

    def list_files(self, *, limit: int = 50) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        p = self._param()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    SELECT id, object_key, filename, storage, bucket, public_url, size_bytes, content_type, sha256, created_at, updated_at
                    FROM hive_files
                    ORDER BY updated_at DESC
                    LIMIT {p}
                    """,
                    (_int_or_none(limit) or 50,),
                )
                rows = self._fetch_dicts(cur)
            return {"ok": True, "enabled": True, "count": len(rows), "files": rows}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "enabled": True, "error": str(exc)}

    def cost_summary(self, *, by_model_limit: int = 20) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        p = self._param()
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT
                      COUNT(*) AS event_count,
                      COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost_usd
                    FROM hive_cost_events
                    """
                )
                totals = self._fetch_dicts(cur)[0]
                cur.execute(
                    f"""
                    SELECT
                      model,
                      provider,
                      COUNT(*) AS event_count,
                      COALESCE(SUM(total_tokens), 0) AS total_tokens,
                      COALESCE(SUM(cost_usd), 0) AS cost_usd
                    FROM hive_cost_events
                    GROUP BY model, provider
                    ORDER BY cost_usd DESC, total_tokens DESC
                    LIMIT {p}
                    """,
                    (_int_or_none(by_model_limit) or 20,),
                )
                by_model = self._fetch_dicts(cur)
            return {"ok": True, "enabled": True, "totals": totals, "by_model": by_model}
        except Exception as exc:  # pragma: no cover
            return {"ok": False, "enabled": True, "error": str(exc)}

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
            """
            CREATE INDEX IF NOT EXISTS idx_hive_cost_events_conversation
            ON hive_cost_events (conversation_id)
            """,
        ]

    def _fetch_dicts(self, cur: Any) -> list[dict[str, Any]]:
        columns = [item[0] for item in cur.description or []]
        return [dict(zip(columns, row)) for row in cur.fetchall()]

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self.dialect == "sqlite":
            path = self._sqlite_path()
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(":memory:" if str(path) == ":memory:" else path)
            try:
                yield conn
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
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
                timeout_sql = self._postgres_statement_timeout_sql()
                if timeout_sql:
                    # PostgreSQL does not accept bind parameters for SET statements
                    # (SET statement_timeout = $1 fails). The value is a sanitised
                    # integer derived from configuration, so this literal SQL is safe.
                    with conn.cursor() as cur:
                        cur.execute(timeout_sql)
                yield conn
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                conn.close()
            return

        raise RuntimeError(f"Unsupported SQL database URL: {self.url!r}")

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        with self._connect() as conn:
            cur = conn.cursor()
            try:
                yield cur
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()


    def _postgres_statement_timeout_sql(self) -> str | None:
        """Return safe literal SQL for PostgreSQL statement timeout.

        PostgreSQL utility commands such as SET do not support bind
        placeholders in the same way as DML queries. psycopg sends
        ``SET statement_timeout = %s`` as ``SET statement_timeout = $1``,
        which PostgreSQL rejects. This method converts the configured
        seconds to a non-negative integer millisecond literal.
        """

        statement_timeout_ms = int(max(0, self.settings.database_statement_timeout_seconds) * 1000)
        if statement_timeout_ms <= 0:
            return None
        return f"SET statement_timeout = {statement_timeout_ms}"

    def _sqlite_path(self) -> Path:
        value = self.url
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if value.startswith(prefix):
                stripped = value.removeprefix(prefix)
                return Path(":memory:") if stripped == ":memory:" else Path(stripped)
        if value in {"sqlite://", "sqlite:///:memory:", ":memory:"}:
            return Path(":memory:")
        return Path(value)

    def _param(self) -> str:
        return "%s" if self.dialect == "postgres" else "?"

    def _select_one_sql(self) -> str:
        return "SELECT 1"

    def _upsert_conversation(self, cur: Any, conv_id: str, mode: str, model: str | None, now: str) -> None:
        p = self._param()
        cur.execute(
            f"""
            INSERT INTO hive_conversations (id, mode, model, created_at, updated_at)
            VALUES ({p}, {p}, {p}, {p}, {p})
            ON CONFLICT(id) DO UPDATE SET
              mode=excluded.mode,
              model=excluded.model,
              updated_at=excluded.updated_at
            """,
            (conv_id, mode, model, now, now),
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
            (str(uuid.uuid4()), conv_id, role, content or "", model, provider, total_tokens, cost, metadata_json, created),
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
        cur.execute(
            f"""
            INSERT INTO hive_files
            (id, object_key, filename, storage, bucket, public_url, size_bytes, content_type, sha256, metadata_json, created_at, updated_at)
            VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p}, {p})
            ON CONFLICT(object_key) DO UPDATE SET
              filename=excluded.filename,
              storage=excluded.storage,
              bucket=excluded.bucket,
              public_url=excluded.public_url,
              size_bytes=excluded.size_bytes,
              content_type=excluded.content_type,
              sha256=excluded.sha256,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            values,
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


def _json_or_none(value: Any) -> Any:
    if value in {None, ""}:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


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
