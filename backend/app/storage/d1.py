from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import Settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class D1MetadataStore:
    """Optional Cloudflare D1 metadata layer for ecosystem indexes.

    This is intentionally separate from the SQL conversation store. HIVE can use
    Postgres for operational chat history and D1 for lightweight ecosystem indexes.
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
        }

    def diagnostics(self) -> dict[str, object]:
        payload: dict[str, object] = {"ok": True, **self.safe_config()}
        if not self.enabled:
            payload["probe"] = {"ok": False, "message": "D1 metadata store disabled or not configured."}
            return payload
        result = self.query("SELECT 1 AS ok", [])
        payload["probe"] = result
        payload["ok"] = bool(result.get("ok"))
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
        ]
        results = [self.query(statement, []) for statement in statements]
        ok = all(bool(item.get("ok")) for item in results)
        return {"ok": ok, "enabled": True, "tables": ["hive_ecosystem_metadata"], "results": results}

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
        try:
            with httpx.Client(timeout=self.settings.d1_timeout_seconds) as client:
                response = client.post(endpoint, headers=headers, json=body)
            payload = response.json() if response.content else {}
            if response.status_code >= 400:
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "message": _d1_error_message(payload) or response.text,
                }
            return {
                "ok": bool(payload.get("success", True)),
                "status_code": response.status_code,
                "result": payload.get("result"),
                "errors": payload.get("errors") or [],
            }
        except Exception as exc:  # pragma: no cover - network only
            return {"ok": False, "message": str(exc)}


def _d1_error_message(payload: dict[str, Any]) -> str:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first)
        return str(first)
    return ""
