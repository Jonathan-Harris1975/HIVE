from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.config import Settings

# Phase 2 - Repository Memory. Cloudflare AI Search (instance: hive-repositories
# by default) lets Repository Memory documents be queried semantically without
# loading a repository's full working copy.
#
# NOTE: Cloudflare's AI Search / AutoRAG REST surface has moved over time; the
# endpoint path below (`/autorag/rags/{instance}/search`) matches the
# documented v4 AutoRAG search endpoint at the time this adapter was written,
# but should be reconfirmed against current Cloudflare API docs before this
# is relied on in production, in the same way any external API integration
# should be verified rather than assumed.


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"raw_text": response.text}


def _cloudflare_error_text(payload: Any) -> str | None:
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("message"):
                return str(first["message"])
    return None


class AiSearchClient:
    """Cloudflare AI Search adapter used to index and query Repository Memory."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(
            settings.ai_search_enabled
            and settings.ai_search_account_id
            and settings.ai_search_api_token
            and settings.ai_search_instance
        )

    @property
    def base_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.settings.ai_search_account_id}/autorag/rags/{self.settings.ai_search_instance}"
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.ai_search_api_token}"}

    @property
    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "account_configured": bool(self.settings.ai_search_account_id),
            "api_token_configured": bool(self.settings.ai_search_api_token),
            "instance": self.settings.ai_search_instance or None,
            "timeout_seconds": self.settings.ai_search_timeout_seconds,
            "top_k": self.settings.ai_search_top_k,
        }

    async def diagnostics(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.enabled,
            "configured": self.enabled,
            "status": "ok" if self.enabled else "not_configured",
            **self.safe_config,
        }
        if not self.enabled:
            payload["reason"] = "AI Search disabled or not fully configured."
        return payload

    async def search(self, query: str, *, top_k: int | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {
                "ok": False,
                "enabled": False,
                "reason": "AI Search disabled or not configured.",
                "matches": [],
            }
        payload = {"query": query, "max_num_results": top_k or self.settings.ai_search_top_k}
        result = await self._request("POST", f"{self.base_url}/search", json_payload=payload)
        raw = result.get("raw")
        matches = []
        if isinstance(raw, dict):
            data = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            matches = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), list) else []
        result["matches"] = matches or []
        result["count"] = len(result["matches"])
        return result

    async def _request(
        self, method: str, url: str, *, json_payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        attempts = max(1, int(self.settings.ai_search_max_attempts))
        timeout = max(1.0, float(self.settings.ai_search_timeout_seconds))
        last: dict[str, Any] | None = None
        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.request(method, url, headers=self.headers, json=json_payload)
                raw = _safe_json(response)
                ok = response.status_code < 400 and bool(
                    raw.get("success", True) if isinstance(raw, dict) else True
                )
                result = {
                    "ok": ok,
                    "enabled": True,
                    "status_code": response.status_code,
                    "attempt": attempt,
                    "raw": raw,
                    "error": None if ok else _cloudflare_error_text(raw) or response.text,
                }
                if ok:
                    return result
                last = result
                # Non-retryable client errors (bad request, auth, not found,
                # forbidden) will fail identically on every retry - stop
                # burning the attempt budget and surface the error now.
                # Only 429 (rate limited) and 5xx (transient server issues)
                # are worth retrying.
                if response.status_code < 500 and response.status_code != 429:
                    return result
                if attempt < attempts:
                    await self._sleep_before_retry(attempt, response=response)
            except httpx.HTTPError as error:
                last = {
                    "ok": False,
                    "enabled": True,
                    "status_code": None,
                    "attempt": attempt,
                    "raw": None,
                    "error": str(error),
                }
                if attempt < attempts:
                    await self._sleep_before_retry(attempt)
        return last or {"ok": False, "enabled": True, "error": "AI Search request failed."}

    @staticmethod
    async def _sleep_before_retry(attempt: int, *, response: "httpx.Response | None" = None) -> None:
        """Exponential backoff with jitter; honours Cloudflare's Retry-After
        header on 429 responses instead of guessing at a delay."""
        if response is not None and response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    delay = min(30.0, max(0.0, float(retry_after)))
                    await asyncio.sleep(delay)
                    return
                except ValueError:
                    pass
        base_delay = min(8.0, 0.5 * (2 ** (attempt - 1)))
        await asyncio.sleep(base_delay + random.uniform(0, 0.25))
