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
            payload["probe"] = {"ok": False, "message": "D1 metadata store disabled or not configured."}
            return payload
        result = self.query("SELECT 1 AS ok", [])
        payload["probe"] = result
        payload["ok"] = bool(result.get("ok"))
        if payload["ok"]:
            counts = self.table_counts()
            payload["table_counts"] = counts
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

    def query(self, sql: str, params: list[Any] | None = None) -> dict[str, object]:
        if not self.enabled:
            return {"ok": False, "message": "D1 metadata store disabled or not configured."}
        endpoint = (
            f"https://api.cloudflare.com/client/v4/accounts/{self.settings.d1_account_id}"
            f"/d1/database/{self.settings.d1_database_id}/query"
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
