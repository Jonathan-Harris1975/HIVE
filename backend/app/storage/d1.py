from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class D1MetadataStore:
    """Optional Cloudflare D1 metadata layer for ecosystem indexes.

    D1 is kept separate from the SQL conversation store. It stores lightweight,
    queryable ecosystem metadata such as audit indexes, council report indexes,
    podcast episode indexes, ebook catalogue cache records, and social snapshots.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(
            self.settings.d1_enabled
            and self.settings.d1_account_id
            and self.settings.d1_database_id
            and self.settings.d1_api_key
        )

    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "account_id_configured": bool(self.settings.d1_account_id),
            "database_id_configured": bool(self.settings.d1_database_id),
            "database_name": self.settings.d1_database_name or None,
            "api_key_configured": bool(self.settings.d1_api_key),
            "timeout_seconds": self.settings.d1_timeout_seconds,
            "max_attempts": self.settings.d1_max_attempts,
        }

    def diagnostics(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": True, **self.safe_config()}
        if not self.enabled:
            payload["ok"] = False
            payload["schema_ready"] = False
            payload["probe"] = {"ok": False, "message": "D1 metadata store disabled or not configured."}
            return payload
        result = self.query("SELECT 1 AS ok", [])
        payload["probe"] = result
        payload["ok"] = bool(result.get("ok"))
        payload["schema_ready"] = False
        if payload["ok"]:
            counts = self.table_counts()
            payload["table_counts"] = counts
            payload["schema_ready"] = bool(counts.get("ok"))
            payload["ok"] = bool(payload["ok"] and payload["schema_ready"])
        return payload

    def init_schema(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "message": "D1 metadata store disabled or not configured."}
        statements = [
            """
            CREATE TABLE IF NOT EXISTS hive_ecosystem_metadata (
                id TEXT PRIMARY KEY,
                lane TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_id TEXT,
                title TEXT,
                url TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hive_ecosystem_metadata_lane
            ON hive_ecosystem_metadata (lane, source_type, updated_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_hive_ecosystem_metadata_source
            ON hive_ecosystem_metadata (source_type, source_id)
            """,
        ]
        results = [self.query(statement, []) for statement in statements]
        ok = all(bool(item.get("ok")) for item in results)
        return {"ok": ok, "enabled": True, "tables": ["hive_ecosystem_metadata"], "results": results}

    def ping_write(self) -> dict[str, object]:
        """Verify D1 can write and delete a probe row."""

        if not self.enabled:
            return {"ok": False, "enabled": False}
        probe_id = f"d1-probe-{int(time.time() * 1000)}"
        now = _now()
        insert = self.query(
            """
            INSERT INTO hive_ecosystem_metadata
            (id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [probe_id, "diagnostic", "d1_write_probe", probe_id, "D1 write probe", None, "{}", now, now],
        )
        delete = self.query("DELETE FROM hive_ecosystem_metadata WHERE id = ?", [probe_id]) if insert.get("ok") else {"ok": False, "skipped": True}
        return {
            "ok": bool(insert.get("ok") and delete.get("ok")),
            "enabled": True,
            "probe_id": probe_id,
            "insert": insert,
            "delete": delete,
        }

    def table_counts(self) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        result = self.query("SELECT COUNT(*) AS count FROM hive_ecosystem_metadata", [])
        if not result.get("ok"):
            return result
        rows = _extract_d1_rows(result.get("result"))
        count = rows[0].get("count") if rows else None
        return {"ok": True, "enabled": True, "counts": {"hive_ecosystem_metadata": count}}

    def upsert_metadata(
        self,
        *,
        item_id: str,
        lane: str,
        source_type: str,
        source_id: str | None,
        title: str | None,
        url: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "enabled": False}
        now = _now()
        sql = """
            INSERT INTO hive_ecosystem_metadata
            (id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              lane=excluded.lane,
              source_type=excluded.source_type,
              source_id=excluded.source_id,
              title=excluded.title,
              url=excluded.url,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
        """
        return self.query(
            sql,
            [
                item_id,
                lane,
                source_type,
                source_id,
                title,
                url,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                now,
                now,
            ],
        )

    def list_metadata(self, *, lane: str | None = None, limit: int = 50) -> dict[str, object]:
        """List recent ecosystem metadata records from D1."""

        if not self.enabled:
            return {"ok": False, "enabled": False}
        safe_limit = max(1, min(int(limit or 50), 500))
        if lane:
            result = self.query(
                """
                SELECT id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at
                FROM hive_ecosystem_metadata
                WHERE lane = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [lane, safe_limit],
            )
        else:
            result = self.query(
                """
                SELECT id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at
                FROM hive_ecosystem_metadata
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [safe_limit],
            )
        if not result.get("ok"):
            return result
        rows = _extract_d1_rows(result.get("result"))
        for row in rows:
            row["metadata"] = _json_or_none(row.pop("metadata_json", None))
        return {"ok": True, "enabled": True, "count": len(rows), "items": rows}


    def search_metadata(self, *, query: str, lane: str | None = None, limit: int = 50) -> dict[str, object]:
        """Search lightweight ecosystem metadata using bounded LIKE matching.

        This intentionally avoids D1 FTS requirements so v1.7 works on the
        existing hive_ecosystem_metadata table.
        """

        if not self.enabled:
            return {"ok": False, "enabled": False}
        safe_limit = max(1, min(int(limit or 50), 500))
        q = f"%{(query or '').strip()}%"
        if lane:
            result = self.query(
                """
                SELECT id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at
                FROM hive_ecosystem_metadata
                WHERE lane = ?
                  AND (
                    title LIKE ? OR source_type LIKE ? OR source_id LIKE ? OR url LIKE ? OR metadata_json LIKE ?
                  )
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [lane, q, q, q, q, q, safe_limit],
            )
        else:
            result = self.query(
                """
                SELECT id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at
                FROM hive_ecosystem_metadata
                WHERE title LIKE ? OR source_type LIKE ? OR source_id LIKE ? OR url LIKE ? OR metadata_json LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                [q, q, q, q, q, safe_limit],
            )
        if not result.get("ok"):
            return result
        rows = _extract_d1_rows(result.get("result"))
        for row in rows:
            row["metadata"] = _json_or_none(row.pop("metadata_json", None))
        return {"ok": True, "enabled": True, "query": query, "lane": lane, "count": len(rows), "items": rows}


    def delete_metadata_ids(self, item_ids: list[str]) -> dict[str, object]:
        """Delete ecosystem metadata rows by id with per-row reporting."""

        if not self.enabled:
            return {"ok": False, "enabled": False, "message": "D1 metadata store disabled or not configured."}
        clean_ids = [str(item_id).strip() for item_id in item_ids if str(item_id).strip()]
        if not clean_ids:
            return {"ok": True, "enabled": True, "deleted_count": 0, "deleted_ids": [], "failed": []}
        deleted_ids: list[str] = []
        failed: list[dict[str, object]] = []
        for item_id in clean_ids:
            result = self.query("DELETE FROM hive_ecosystem_metadata WHERE id = ?", [item_id])
            if result.get("ok"):
                deleted_ids.append(item_id)
            else:
                failed.append({"id": item_id, "result": result})
        return {
            "ok": not failed,
            "enabled": True,
            "requested_count": len(clean_ids),
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "failed": failed,
        }

    def reset_configured_databases(self) -> dict[str, object]:
        """Purge application rows from the configured ecosystem D1 databases.

        The databases themselves, their schemas and schema-migration ledgers are
        deliberately preserved. This keeps deployed bindings/UUIDs stable and avoids
        replaying additive migrations against an already-created schema.
        """

        if not self.settings.d1_account_id or not self.settings.d1_api_key:
            return {
                "ok": False,
                "enabled": False,
                "message": "D1 account credentials are not configured.",
            }

        names = list(dict.fromkeys(
            item.strip() for item in self.settings.d1_reset_database_names if item.strip()
        ))
        if not names:
            return {
                "ok": False,
                "enabled": True,
                "message": "No D1 reset database names are configured.",
            }

        results: list[dict[str, object]] = []
        for name in names:
            resolved = self._resolve_database_by_name(name)
            if not resolved.get("ok"):
                results.append({"database_name": name, **resolved})
                continue
            database_id = str(resolved.get("database_id") or "")
            results.append(self._purge_database(database_id=database_id, database_name=name))

        return {
            "ok": bool(results) and all(bool(item.get("ok")) for item in results),
            "enabled": True,
            "database_names": names,
            "databases": results,
        }

    def _resolve_database_by_name(self, name: str) -> dict[str, object]:
        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.settings.d1_account_id}"
            "/d1/database"
        )
        headers = {"Authorization": f"Bearer {self.settings.d1_api_key}"}
        try:
            with httpx.Client(timeout=self.settings.d1_timeout_seconds) as client:
                response = client.get(endpoint, headers=headers, params={"name": name, "per_page": 10})
            payload = response.json() if response.content else {}
        except Exception as exc:  # pragma: no cover - network only
            return {"ok": False, "message": str(exc), "error_type": type(exc).__name__}

        if response.status_code >= 400 or not isinstance(payload, dict):
            return {
                "ok": False,
                "status_code": response.status_code,
                "message": _d1_error_message(payload) if isinstance(payload, dict) else response.text,
            }

        matches = [
            item for item in payload.get("result", [])
            if isinstance(item, dict) and str(item.get("name") or "") == name
        ]
        if len(matches) != 1:
            return {
                "ok": False,
                "status_code": response.status_code,
                "message": (
                    f"Expected exactly one D1 database named {name!r}; found {len(matches)}."
                ),
            }
        database_id = str(matches[0].get("uuid") or "").strip()
        if not database_id:
            return {"ok": False, "message": f"D1 database {name!r} did not return a UUID."}
        return {"ok": True, "database_id": database_id}

    def _purge_database(self, *, database_id: str, database_name: str) -> dict[str, object]:
        tables_result = self._query_database(
            database_id,
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
              AND name NOT LIKE '_cf_%'
            ORDER BY name
            """,
            [],
        )
        if not tables_result.get("ok"):
            return {"ok": False, "database_name": database_name, "error": tables_result}

        rows = _extract_d1_rows(tables_result.get("result"))
        tables = [str(row.get("name") or "").strip() for row in rows]
        tables = [name for name in tables if name]
        preserved = [name for name in tables if _preserve_d1_table(name)]
        purge_tables = [name for name in tables if name not in preserved]

        if not purge_tables:
            return {
                "ok": True,
                "database_name": database_name,
                "tables_cleared": [],
                "preserved_tables": preserved,
            }

        # D1 does not support binding table identifiers. Names come only from sqlite_master
        # and are escaped by _quote_identifier, so this dynamic SQL cannot break out of the
        # quoted identifier context.
        delete_statements = [
            f'DELETE FROM {_quote_identifier(name)}'  # nosec B608
            for name in purge_tables
        ]
        delete_sql = ";\n".join(delete_statements)
        purge_result = self._query_database(
            database_id,
            f"PRAGMA defer_foreign_keys = ON;\n{delete_sql};\nPRAGMA defer_foreign_keys = OFF;",
            [],
        )
        return {
            "ok": bool(purge_result.get("ok")),
            "database_name": database_name,
            "tables_cleared": purge_tables if purge_result.get("ok") else [],
            "preserved_tables": preserved,
            "result": purge_result,
        }

    def query(self, sql: str, params: list[Any] | None = None) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "message": "D1 metadata store disabled or not configured."}
        return self._query_database(self.settings.d1_database_id, sql, params)

    def _query_database(
        self, database_id: str, sql: str, params: list[Any] | None = None
    ) -> dict[str, object]:
        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.settings.d1_account_id}"
            f"/d1/database/{database_id}/query"
        )
        headers = {
            "Authorization": f"Bearer {self.settings.d1_api_key}",
            "Content-Type": "application/json",
        }
        body = {"sql": sql, "params": params or []}
        attempts = max(1, int(self.settings.d1_max_attempts or 1))
        last_error: dict[str, object] | None = None
        for attempt in range(1, attempts + 1):
            try:
                with httpx.Client(timeout=self.settings.d1_timeout_seconds) as client:
                    response = client.post(endpoint, headers=headers, json=body)
                payload = response.json() if response.content else {}
                if response.status_code >= 400:
                    last_error = {
                        "ok": False,
                        "status_code": response.status_code,
                        "attempt": attempt,
                        "message": _d1_error_message(payload) or response.text,
                        "errors": payload.get("errors") if isinstance(payload, dict) else None,
                    }
                else:
                    return {
                        "ok": bool(payload.get("success", True)),
                        "status_code": response.status_code,
                        "attempt": attempt,
                        "result": payload.get("result"),
                        "errors": payload.get("errors") or [],
                    }
            except Exception as exc:  # pragma: no cover - network only
                last_error = {"ok": False, "attempt": attempt, "message": str(exc), "error_type": type(exc).__name__}
            if attempt < attempts:
                time.sleep(min(0.25 * attempt, 1.0))
        return last_error or {"ok": False, "message": "Unknown D1 request failure"}


def _extract_d1_rows(result: Any) -> list[dict[str, Any]]:
    """Cloudflare D1 REST responses normally wrap rows under result[0].results."""

    if isinstance(result, list):
        rows: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                nested = item.get("results")
                if isinstance(nested, list):
                    rows.extend(row for row in nested if isinstance(row, dict))
                elif all(key in item for key in ("id", "lane", "source_type")):
                    rows.append(item)
        return rows
    if isinstance(result, dict):
        nested = result.get("results")
        if isinstance(nested, list):
            return [row for row in nested if isinstance(row, dict)]
    return []


def _preserve_d1_table(name: str) -> bool:
    lowered = name.lower()
    return lowered == "d1_migrations" or lowered.endswith("_schema_migrations")


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json_or_none(value: Any) -> Any:
    if value in {None, ""}:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _d1_error_message(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
        return str(first)
    return ""
