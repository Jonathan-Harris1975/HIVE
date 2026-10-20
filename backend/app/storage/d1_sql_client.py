"""Small synchronous DB-API facade over Cloudflare's D1 HTTP API.

Writes are buffered and committed with D1's transactional batch endpoint. Reads
execute immediately; a read after pending writes flushes that batch first.
"""

from __future__ import annotations

import re
from typing import Any

import httpx


class D1DatabaseError(RuntimeError):
    """Raised when Cloudflare rejects a D1 query or batch."""


class D1Connection:
    def __init__(
        self,
        *,
        account_id: str,
        database_id: str,
        api_key: str,
        timeout_seconds: int = 3,
    ) -> None:
        self.endpoint = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{account_id}/d1/database/{database_id}/query"
        )
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self.timeout_seconds = timeout_seconds
        self._pending: list[tuple[dict[str, Any], D1Cursor]] = []
        self._closed = False

    def cursor(self) -> "D1Cursor":
        if self._closed:
            raise D1DatabaseError("D1 connection is closed")
        return D1Cursor(self)

    def commit(self) -> list[dict[str, Any]]:
        self._ensure_open()
        if not self._pending:
            return []
        pending, self._pending = self._pending, []
        statements = [statement for statement, _cursor in pending]
        if len(statements) == 1:
            payload: dict[str, Any] = statements[0]
        else:
            payload = {"batch": statements}
        try:
            results = self._request(payload)
        except Exception:
            for _statement, cursor in pending:
                cursor._clear_pending()
            raise
        for index, ((_statement, cursor), result) in enumerate(zip(pending, results)):
            cursor._settle_pending(index, result)
        return results

    def rollback(self) -> None:
        self._pending.clear()

    def close(self) -> None:
        self._pending.clear()
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise D1DatabaseError("D1 connection is closed")

    def _request(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        self._ensure_open()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(self.endpoint, headers=self.headers, json=payload)
            body = response.json() if response.content else {}
        except Exception as exc:
            raise D1DatabaseError(f"D1 request failed: {type(exc).__name__}") from exc

        if response.status_code >= 400 or not isinstance(body, dict) or not body.get("success"):
            errors = body.get("errors") if isinstance(body, dict) else None
            message = "D1 query failed"
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    message = str(first.get("message") or message)
            raise D1DatabaseError(message)

        result = body.get("result")
        if not isinstance(result, list):
            raise D1DatabaseError("D1 returned an invalid query response")
        for item in result:
            if not isinstance(item, dict) or not item.get("success", True):
                raise D1DatabaseError("D1 batch statement failed")
        return result


class D1Cursor:
    def __init__(self, connection: D1Connection) -> None:
        self.connection = connection
        self.description: list[tuple[str, None, None, None, None, None, None]] | None = None
        self._rows: list[tuple[Any, ...]] = []
        self._row_index = 0
        self._rowcount = -1
        self._pending_index: int | None = None

    def execute(self, sql: str, params: Any = None) -> "D1Cursor":
        statement = {"sql": sql, "params": list(params or [])}
        self.description = None
        self._rows = []
        self._row_index = 0
        self._rowcount = -1
        self._pending_index = None

        if _is_read_statement(sql):
            self.connection.commit()
            result = self.connection._request(statement)[0]
            self._set_result(result)
            return self

        self.connection._pending.append((statement, self))
        self._pending_index = len(self.connection._pending) - 1
        return self

    def executemany(self, sql: str, seq_of_params: Any) -> "D1Cursor":
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    @property
    def rowcount(self) -> int:
        if self._pending_index is not None:
            self._flush_pending()
        return self._rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        if self._pending_index is not None:
            self._flush_pending()
        if self._row_index >= len(self._rows):
            return None
        row = self._rows[self._row_index]
        self._row_index += 1
        return row

    def fetchall(self) -> list[tuple[Any, ...]]:
        if self._pending_index is not None:
            self._flush_pending()
        rows = self._rows[self._row_index :]
        self._row_index = len(self._rows)
        return rows

    def close(self) -> None:
        return None

    def flush_batch(self) -> list[int]:
        """Commit queued writes together and return their change counts in order."""
        pending_index = self._pending_index
        if pending_index is None:
            return []
        results = self.connection.commit()
        counts: list[int] = []
        for result in results:
            meta = result.get("meta")
            changes = meta.get("changes") if isinstance(meta, dict) else None
            counts.append(int(changes) if isinstance(changes, (int, float)) else 0)
        if pending_index < len(results) and self._pending_index is not None:
            self._set_result(results[pending_index])
            self._pending_index = None
        return counts

    def _set_result(self, result: dict[str, Any]) -> None:
        raw_rows = result.get("results")
        raw_rows = raw_rows if isinstance(raw_rows, list) else []
        keys: list[str] = []
        if raw_rows and isinstance(raw_rows[0], dict):
            keys = list(raw_rows[0])
        self.description = [(key, None, None, None, None, None, None) for key in keys]
        self._rows = [tuple(row.get(key) for key in keys) for row in raw_rows if isinstance(row, dict)]
        meta = result.get("meta")
        changes = meta.get("changes") if isinstance(meta, dict) else None
        self._rowcount = int(changes) if isinstance(changes, (int, float)) else len(self._rows)

    def _flush_pending(self) -> None:
        self.flush_batch()

    def _settle_pending(self, index: int, result: dict[str, Any]) -> None:
        if self._pending_index == index:
            self._set_result(result)
            self._pending_index = None

    def _clear_pending(self) -> None:
        self._pending_index = None


def _is_read_statement(sql: str) -> bool:
    statement = sql.lstrip().upper()
    while statement.startswith("--"):
        _, _, statement = statement.partition("\n")
        statement = statement.lstrip()
    if statement.startswith(("SELECT", "PRAGMA", "EXPLAIN")):
        return True
    if statement.startswith("WITH"):
        return _with_main_statement_is_read(statement)
    return False


def _with_main_statement_is_read(statement: str) -> bool:
    """Recognise read-only CTEs without classifying a CTE-wrapped write as a read."""

    depth = 0
    quote: str | None = None
    index = 0
    while index < len(statement):
        char = statement[index]
        following = statement[index + 1] if index + 1 < len(statement) else ""
        if quote:
            if char == quote:
                if following == quote:
                    index += 2
                    continue
                quote = None
            elif char == "\\" and quote == "'":
                index += 2
                continue
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "-" and following == "-":
            newline = statement.find("\n", index + 2)
            index = len(statement) if newline < 0 else newline
            continue
        elif char == "/" and following == "*":
            end = statement.find("*/", index + 2)
            index = len(statement) if end < 0 else end + 2
            continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
            if depth == 0:
                suffix = statement[index + 1 :].lstrip()
                if suffix.startswith(","):
                    index += 1
                    continue
                match = re.match(r"([A-Z]+)\b", suffix)
                if match:
                    return match.group(1) in {"SELECT", "VALUES"}
        index += 1
    return False
