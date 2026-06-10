from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.config import Settings


class VectorizeClient:
    """Cloudflare Vectorize REST adapter.

    SQL chunk IDs are used as Vectorize IDs, so PostgreSQL remains the source of
    truth and Vectorize is only a semantic lookup accelerator.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(
            settings.vectorize_enabled
            and settings.vectorize_account_id
            and settings.vectorize_api_token
            and settings.vectorize_index_name
        )

    @property
    def base_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.settings.vectorize_account_id}/vectorize/v2/indexes/{self.settings.vectorize_index_name}"
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.vectorize_api_token}"}

    @property
    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "account_configured": bool(self.settings.vectorize_account_id),
            "api_token_configured": bool(self.settings.vectorize_api_token),
            "index_name": self.settings.vectorize_index_name or None,
            "timeout_seconds": self.settings.vectorize_timeout_seconds,
            "max_attempts": self.settings.vectorize_max_attempts,
            "top_k": self.settings.vectorize_top_k,
            "return_metadata": self.settings.vectorize_return_metadata,
        }

    async def diagnostics(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.enabled,
            **self.safe_config,
            "info_probe": None,
            "index_stats": None,
        }
        if not self.enabled:
            payload["reason"] = "Vectorize disabled or not fully configured."
            return payload
        payload["info_probe"] = await self.info()
        payload["ok"] = bool(isinstance(payload["info_probe"], dict) and payload["info_probe"].get("ok"))
        payload["index_stats"] = _extract_index_stats(payload.get("info_probe"))
        return payload

    async def info(self) -> dict[str, object]:
        return await self._request("GET", f"{self.base_url}/info")

    async def upsert_vectors(self, vectors: list[dict[str, Any]]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "reason": "Vectorize disabled or not configured."}
        if not vectors:
            return {"ok": False, "enabled": True, "reason": "No vectors supplied."}
        ndjson = "\n".join(json.dumps(vector, ensure_ascii=False, default=str) for vector in vectors) + "\n"
        files = {"vectors": ("vectors.ndjson", ndjson.encode("utf-8"), "application/x-ndjson")}
        return await self._request(
            "POST",
            f"{self.base_url}/upsert?unparsable-behavior=error",
            files=files,
        )

    async def query(
        self,
        vector: list[float],
        *,
        top_k: int | None = None,
        metadata_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "reason": "Vectorize disabled or not configured.", "matches": []}
        payload: dict[str, Any] = {
            "vector": vector,
            "topK": top_k or self.settings.vectorize_top_k,
            "returnMetadata": self.settings.vectorize_return_metadata,
            "returnValues": False,
        }
        if metadata_filter:
            payload["filter"] = metadata_filter
        result = await self._request("POST", f"{self.base_url}/query", json_payload=payload)
        result["matches"] = _extract_matches(result.get("raw"))
        result["count"] = len(result["matches"])
        return result

    async def delete_by_ids(self, ids: list[str]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "enabled": False, "reason": "Vectorize disabled or not configured."}
        return await self._request("POST", f"{self.base_url}/delete_by_ids", json_payload={"ids": ids})

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_payload: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempts = max(1, int(self.settings.vectorize_max_attempts))
        timeout = max(1.0, float(self.settings.vectorize_timeout_seconds))
        last: dict[str, Any] | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(
                        method,
                        url,
                        headers=self.headers,
                        json=json_payload,
                        files=files,
                    )
                raw = _safe_json(response)
                ok = response.status_code < 400 and bool(raw.get("success", True) if isinstance(raw, dict) else True)
                result = {
                    "ok": ok,
                    "enabled": True,
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "raw": raw,
                    "error": None if ok else _cloudflare_error_text(raw) or response.text,
                }
                if ok:
                    result["result"] = raw.get("result") if isinstance(raw, dict) else raw
                    return result
                last = result
            except Exception as exc:  # pragma: no cover - network errors vary
                last = {"ok": False, "enabled": True, "attempt": attempt, "error": str(exc), "type": type(exc).__name__}
        return last or {"ok": False, "enabled": True, "error": "Vectorize request failed."}



def _extract_index_stats(info_probe: Any) -> dict[str, Any]:
    """Return stable, safe index stats from Cloudflare's evolving info shape."""

    stats: dict[str, Any] = {
        "dimensions": None,
        "metric": None,
        "vector_count": None,
        "processed_up_to_mutation": None,
    }
    if not isinstance(info_probe, dict):
        return stats
    raw = info_probe.get("raw")
    result = info_probe.get("result")
    if isinstance(raw, dict) and result is None:
        result = raw.get("result")
    candidates: list[Any] = []
    if isinstance(result, dict):
        candidates.append(result)
        config = result.get("config")
        if isinstance(config, dict):
            candidates.append(config)
        dimensions = result.get("dimensions")
        metric = result.get("metric")
        count = result.get("vector_count") or result.get("vectorCount") or result.get("count")
        mutation = result.get("processedUpToMutation") or result.get("processed_up_to_mutation")
        if dimensions is not None:
            stats["dimensions"] = dimensions
        if metric is not None:
            stats["metric"] = metric
        if count is not None:
            stats["vector_count"] = count
        if mutation is not None:
            stats["processed_up_to_mutation"] = mutation
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for source_key, target_key in (
            ("dimensions", "dimensions"),
            ("dimension", "dimensions"),
            ("metric", "metric"),
            ("vector_count", "vector_count"),
            ("vectorCount", "vector_count"),
            ("count", "vector_count"),
        ):
            if stats.get(target_key) is None and candidate.get(source_key) is not None:
                stats[target_key] = candidate.get(source_key)
    return stats

def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return {"text": response.text}


def _cloudflare_error_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        messages = [str(item.get("message") or item) for item in errors if isinstance(item, dict)]
        return "; ".join(messages) if messages else str(errors)
    return None


def _extract_matches(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result", payload)
    matches = None
    if isinstance(result, dict):
        matches = result.get("matches") or result.get("vectors") or result.get("data")
    elif isinstance(result, list):
        matches = result
    if not isinstance(matches, list):
        return []
    parsed: list[dict[str, Any]] = []
    for item in matches:
        if not isinstance(item, dict):
            continue
        match_id = item.get("id") or item.get("vectorId") or item.get("vector_id")
        if not match_id:
            continue
        parsed.append(
            {
                "id": str(match_id),
                "score": item.get("score") or item.get("similarity") or item.get("distance"),
                "metadata": item.get("metadata") or {},
            }
        )
    return parsed
