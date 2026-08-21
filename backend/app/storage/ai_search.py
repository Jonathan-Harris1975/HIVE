from __future__ import annotations

import asyncio
import json
import random
from typing import Any
from urllib.parse import quote

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


def _result_items(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        for key in ("data", "instances", "items", "results"):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _instance_id(instance: dict[str, Any]) -> str:
    return str(instance.get("id") or instance.get("name") or "").strip()


def _instance_paused(instance: dict[str, Any]) -> bool:
    # Cloudflare's current configuration uses `paused`; older responses have
    # exposed enable/enabled. Treat only an explicit negative/paused value as
    # unavailable so schema additions do not accidentally hide healthy indexes.
    if instance.get("paused") is True:
        return True
    if instance.get("enabled") is False or instance.get("enable") is False:
        return True
    return False


class AiSearchClient:
    """Cloudflare AI Search adapter used by HIVE.

    The configured ``AI_SEARCH_INSTANCE`` remains the deterministic primary for
    repository indexing and backwards-compatible single-index calls. Discovery,
    diagnostics and HIVE-wide semantic search use the account instance list so
    every available AI Search index participates without a hard-coded bucket
    list. Failures are isolated per instance and never make healthy instances
    disappear from the result.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(
            settings.ai_search_enabled
            and settings.ai_search_account_id
            and settings.ai_search_api_token
        )

    @property
    def instances_url(self) -> str:
        return (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.settings.ai_search_account_id}/ai-search/instances"
        )

    @property
    def base_url(self) -> str:
        instance = quote(str(self.settings.ai_search_instance or ""), safe="")
        return f"{self.instances_url}/{instance}"

    def instance_url(self, instance_id: str) -> str:
        return f"{self.instances_url}/{quote(str(instance_id), safe='')}"

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
        return missing

    @property
    def safe_config(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "account_configured": bool(self.settings.ai_search_account_id),
            "api_token_configured": bool(self.settings.ai_search_api_token),
            "primary_instance": self.settings.ai_search_instance or None,
            "search_scope": "all_available_instances",
            "timeout_seconds": self.settings.ai_search_timeout_seconds,
            "top_k": self.settings.ai_search_top_k,
            "missing": self.missing_configuration,
        }

    async def list_instances(self) -> dict[str, Any]:
        if not self.enabled:
            return {
                "ok": False,
                "configured": False,
                **self.safe_config,
                "instances": [],
                "reason": "AI Search disabled or not configured.",
            }

        # Cloudflare's account-level list endpoint is paginated. Request the
        # maximum documented page size and continue until total_count is met so
        # HIVE never silently searches only the first page of an account.
        page = 1
        per_page = 100
        instances: list[dict[str, Any]] = []
        last_result: dict[str, Any] | None = None
        while True:
            separator = "&" if "?" in self.instances_url else "?"
            url = f"{self.instances_url}{separator}page={page}&per_page={per_page}"
            result = await self._request("GET", url)
            last_result = result
            if not result.get("ok"):
                return {
                    **result,
                    "configured": True,
                    "instances": instances,
                    "count": len(instances),
                }
            raw = result.get("raw")
            batch = _result_items(raw)
            instances.extend(batch)
            result_info = raw.get("result_info") if isinstance(raw, dict) else None
            total_count = result_info.get("total_count") if isinstance(result_info, dict) else None
            if not isinstance(total_count, int) or len(instances) >= total_count or not batch:
                break
            page += 1

        return {
            **(last_result or {"ok": True, "enabled": True}),
            "configured": True,
            "instances": instances,
            "count": len(instances),
        }

    async def instance_stats(self, instance_id: str) -> dict[str, Any]:
        """Return indexing statistics for one AI Search instance."""
        if not self.enabled:
            return {"ok": False, "enabled": False, "reason": "AI Search disabled or not configured."}
        result = await self._request("GET", f"{self.instance_url(instance_id)}/stats")
        raw = result.get("raw")
        stats = raw.get("result") if isinstance(raw, dict) and isinstance(raw.get("result"), dict) else {}
        return {**result, "instance": instance_id, "stats": stats}

    async def diagnostics(self) -> dict[str, object]:
        """Return account-wide AI Search health plus the configured primary."""
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
                "instances": [],
            }

        listing = await self.list_instances()
        if listing.get("ok"):
            instances = listing.get("instances", [])
            normalised = []
            for value in instances if isinstance(instances, list) else []:
                if not isinstance(value, dict):
                    continue
                instance_id = _instance_id(value)
                if not instance_id:
                    continue
                normalised.append({
                    "id": instance_id,
                    "name": value.get("name") or instance_id,
                    "paused": _instance_paused(value),
                    "modified_at": value.get("modified_at") or value.get("updated_at"),
                    "last_activity": value.get("last_activity"),
                    "source": value.get("source") or value.get("data_source"),
                })
            # Stats expose indexing errors that are not visible from the
            # instance configuration alone. Fetch them with bounded concurrency
            # so HIVE can distinguish "enabled" from "healthy and indexed".
            semaphore = asyncio.Semaphore(4)

            async def load_stats(item: dict[str, Any]) -> tuple[str, dict[str, Any]]:
                async with semaphore:
                    return item["id"], await self.instance_stats(item["id"])

            stats_results = await asyncio.gather(*(load_stats(item) for item in normalised)) if normalised else []
            stats_by_id = {instance_id: result for instance_id, result in stats_results}
            stats_failures = 0
            indexing_error_count = 0
            degraded_stats_count = 0
            for item in normalised:
                stats_result = stats_by_id.get(item["id"], {})
                stats = stats_result.get("stats") if isinstance(stats_result, dict) else {}
                if not stats_result.get("ok"):
                    stats_failures += 1
                    item["stats_ok"] = False
                    item["stats_error"] = stats_result.get("error") or stats_result.get("reason") or "stats_unavailable"
                    continue
                item["stats_ok"] = True
                item["completed"] = stats.get("completed")
                item["error"] = int(stats.get("error") or 0) if isinstance(stats, dict) else 0
                item["queued"] = stats.get("queued")
                item["running"] = stats.get("running")
                item["outdated"] = stats.get("outdated")
                item["stats_degraded"] = bool(stats.get("degraded"))
                item["last_activity"] = stats.get("last_activity") or item.get("last_activity")
                indexing_error_count += item["error"]
                if item["stats_degraded"]:
                    degraded_stats_count += 1

            active = [item for item in normalised if not item["paused"]]
            paused = [item for item in normalised if item["paused"]]
            primary = str(self.settings.ai_search_instance or "")
            primary_present = any(item["id"] == primary or item["name"] == primary for item in normalised)
            # Availability is deliberately independent from indexing completeness.
            # A failed/unindexed file is an indexing-health signal, not a reason to
            # take semantic search offline while at least one instance can serve.
            ok = bool(active)
            indexing_healthy = not indexing_error_count and not degraded_stats_count and not stats_failures
            degraded = ok and (bool(paused) or not indexing_healthy or (bool(primary) and not primary_present))
            reason = None
            if not normalised:
                reason = "Cloudflare returned no AI Search instances."
            elif not active:
                reason = "All AI Search instances are paused or disabled."
            elif indexing_error_count:
                reason = f"AI Search remains available; {indexing_error_count} indexing error(s) need attention."
            elif degraded_stats_count:
                reason = f"AI Search remains available; {degraded_stats_count} instance(s) report degraded indexing statistics."
            elif stats_failures:
                reason = f"AI Search remains available; indexing statistics could not be verified for {stats_failures} instance(s)."
            elif paused:
                reason = f"AI Search remains available; {len(paused)} instance(s) are paused or disabled."
            elif primary and not primary_present:
                reason = f"AI Search remains available; configured primary instance '{primary}' was not returned by Cloudflare."
            return {
                "ok": ok,
                "configured": True,
                "status": "unavailable" if not ok else ("degraded" if degraded else "ok"),
                "availability_status": "available" if ok else "unavailable",
                "indexing_healthy": indexing_healthy,
                **self.safe_config,
                "instance_count": len(normalised),
                "active_instance_count": len(active),
                "paused_instance_count": len(paused),
                "indexing_error_count": indexing_error_count,
                "degraded_stats_count": degraded_stats_count,
                "stats_failure_count": stats_failures,
                "primary_present": primary_present,
                "instances": normalised,
                "status_code": listing.get("status_code"),
                "reason": reason,
            }

        # A list permission failure should be visible rather than silently
        # pretending the configured primary represents the whole account.
        return {
            "ok": False,
            "configured": True,
            "status": "error",
            **self.safe_config,
            "instance_count": 0,
            "active_instance_count": 0,
            "paused_instance_count": 0,
            "primary_present": False,
            "instances": [],
            "status_code": listing.get("status_code"),
            "reason": listing.get("error") or "AI Search instance discovery failed.",
        }

    async def search(self, query: str, *, top_k: int | None = None, instance_id: str | None = None) -> dict[str, Any]:
        """Search one instance, retaining the original API contract."""
        if not self.enabled:
            return {
                "ok": False,
                "enabled": False,
                "reason": "AI Search disabled or not configured.",
                "matches": [],
            }
        target = str(instance_id or self.settings.ai_search_instance or "").strip()
        if not target:
            return {"ok": False, "enabled": True, "reason": "AI Search instance is not set.", "matches": []}
        # Current Cloudflare AI Search places retrieval controls under
        # ai_search_options.retrieval. Keep the simple text `query` form so the
        # call stays compact while matching the current REST schema.
        payload = {
            "query": query,
            "ai_search_options": {
                "retrieval": {
                    "max_num_results": max(1, min(50, int(top_k or self.settings.ai_search_top_k))),
                }
            },
        }
        result = await self._request("POST", f"{self.instance_url(target)}/search", json_payload=payload)
        matches = self._extract_matches(result.get("raw"))
        result["instance"] = target
        result["matches"] = matches
        result["count"] = len(matches)
        return result

    async def search_all(self, query: str, *, top_k: int | None = None) -> dict[str, Any]:
        """Search every active Cloudflare AI Search instance and merge results."""
        if not self.enabled:
            return {
                "ok": False,
                "enabled": False,
                "reason": "AI Search disabled or not configured.",
                "matches": [],
                "instances": [],
            }

        listing = await self.list_instances()
        discovered = listing.get("instances", []) if listing.get("ok") else []
        instance_ids = [
            _instance_id(item)
            for item in discovered
            if isinstance(item, dict) and _instance_id(item) and not _instance_paused(item)
        ]
        # Preserve a deterministic fallback for accounts whose token can run an
        # instance but does not have list permission. Diagnostics still reports
        # that discovery problem; search remains useful rather than going dark.
        fallback = str(self.settings.ai_search_instance or "").strip()
        if not instance_ids and fallback:
            instance_ids = [fallback]

        instance_ids = list(dict.fromkeys(instance_ids))
        if not instance_ids:
            return {
                "ok": False,
                "enabled": True,
                "reason": listing.get("error") or "No active AI Search instances were discovered.",
                "matches": [],
                "instances": [],
            }

        requested = max(1, int(top_k or self.settings.ai_search_top_k))
        semaphore = asyncio.Semaphore(4)

        async def run(instance_id: str) -> dict[str, Any]:
            async with semaphore:
                return await self.search(query, top_k=requested, instance_id=instance_id)

        results = await asyncio.gather(*(run(instance_id) for instance_id in instance_ids))
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        failures: list[dict[str, Any]] = []
        for instance_id, result in zip(instance_ids, results, strict=True):
            if not result.get("ok"):
                failures.append({
                    "instance": instance_id,
                    "status_code": result.get("status_code"),
                    "error": result.get("error") or result.get("reason") or "search_failed",
                })
                continue
            for match in result.get("matches", []):
                enriched = dict(match) if isinstance(match, dict) else {"value": match}
                enriched["_ai_search_instance"] = instance_id
                identity = str(
                    enriched.get("id")
                    or enriched.get("chunk_id")
                    or enriched.get("url")
                    or json.dumps(enriched, sort_keys=True, default=str)
                )
                if identity in seen:
                    continue
                seen.add(identity)
                merged.append(enriched)

        def score(item: dict[str, Any]) -> float:
            for key in ("score", "similarity", "relevance_score"):
                value = item.get(key)
                if isinstance(value, (int, float)):
                    return float(value)
            return 0.0

        if any(score(item) for item in merged):
            merged.sort(key=score, reverse=True)
        merged = merged[:requested]
        successful = len(instance_ids) - len(failures)
        return {
            "ok": successful > 0,
            "enabled": True,
            "scope": "all_available_instances",
            "instance_count": len(instance_ids),
            "successful_instances": successful,
            "failed_instances": failures,
            "instances": instance_ids,
            "matches": merged,
            "count": len(merged),
            "discovery_ok": bool(listing.get("ok")),
        }

    @staticmethod
    def _extract_matches(raw: Any) -> list[Any]:
        if isinstance(raw, dict):
            data = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            if isinstance(data, dict):
                for key in ("data", "chunks", "results"):
                    candidate = data.get(key)
                    if isinstance(candidate, list):
                        return candidate
        return []

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
