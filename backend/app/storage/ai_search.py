from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx

from app.core.config import Settings


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
    """Cloudflare AI Search adapter used by Repository Memory.

    Uses Cloudflare's current managed AI Search REST surface under
    ``/ai-search/instances``. The older AutoRAG routes remain compatible at
    Cloudflare, but are deliberately not used here so HIVE gets current API
    behaviour and diagnostics.
    """

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
            f"{self.settings.ai_search_account_id}/ai-search/instances/{self.settings.ai_search_instance}"
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.ai_search_api_token}",
            "Content-Type": "application/json",
        }

    @property
    def missing_configuration(self) -> list[str]:
        missing: list[str] = []
        if not self.settings.ai_search_enabled:
            missing.append("AI_SEARCH_ENABLED")
        if not self.settings.ai_search_account_id:
            missing.append("AI_SEARCH_ACCOUNT_ID")
        if not self.settings.ai_search_api_token:
            missing.append("CF_WORKERS_AI_API")
        if not self.settings.ai_search_instance:
            missing.append("AI_SEARCH_INSTANCE")
        return missing

    @property
    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "account_configured": bool(self.settings.ai_search_account_id),
            "api_token_configured": bool(self.settings.ai_search_api_token),
            "instance": self.settings.ai_search_instance or None,
            "timeout_seconds": self.settings.ai_search_timeout_seconds,
            "top_k": self.settings.ai_search_top_k,
            "missing": self.missing_configuration,
        }

    async def diagnostics(self) -> dict[str, object]:
        if not self.enabled:
            return {
                "ok": False,
                "configured": False,
                "status": "not_configured",
                **self.safe_config,
                "reason": (
                    "Missing runtime setting(s): " + ", ".join(self.missing_configuration)
                    if self.missing_configuration
                    else "AI Search is not fully configured."
                ),
            }

        result = await self._request("GET", self.base_url)
        ok = bool(result.get("ok"))
        raw = result.get("raw")
        instance: dict[str, Any] = {}
        if isinstance(raw, dict) and isinstance(raw.get("result"), dict):
            value = raw["result"]
            instance = {
                "id": value.get("id"),
                "enable": value.get("enable"),
                "last_activity": value.get("last_activity"),
                "modified_at": value.get("modified_at"),
            }
        return {
            "ok": ok,
            "configured": True,
            "status": "ok" if ok else "error",
            **self.safe_config,
            "instance_status": instance,
            "status_code": result.get("status_code"),
            "reason": None if ok else result.get("error"),
        }

    async def search(self, query: str, *, top_k: int | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {
                "ok": False,
                "enabled": False,
                "reason": "AI Search disabled or not configured.",
                "matches": [],
            }
        payload = {
            "query": query,
            "max_num_results": top_k or self.settings.ai_search_top_k,
        }
        result = await self._request("POST", f"{self.base_url}/search", json_payload=payload)
        raw = result.get("raw")
        matches: list[Any] = []
        if isinstance(raw, dict):
            data = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            if isinstance(data, dict):
                for key in ("data", "chunks", "results"):
                    candidate = data.get(key)
                    if isinstance(candidate, list):
                        matches = candidate
                        break
        result["matches"] = matches
        result["count"] = len(matches)
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
