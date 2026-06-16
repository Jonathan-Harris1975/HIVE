from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.production import build_readiness_report
from app.core.version import BUILD_STAGE


@dataclass(frozen=True)
class ProbeTarget:
    repo: str
    label: str
    category: str
    description: str
    health_url: str
    operational_url: str = ""
    operational_token: str = ""


_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_LOCK = asyncio.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean_url(value: str) -> str:
    return (value or "").strip()


def _targets(settings: Settings) -> list[ProbeTarget]:
    return [
        ProbeTarget(
            repo="HIVE-UI",
            label="HIVE-UI",
            category="frontend",
            description="Cloudflare Pages operator interface",
            health_url=_clean_url(settings.hive_ui_health_url),
        ),
        ProbeTarget(
            repo="AIMS",
            label="AIMS",
            category="background_api",
            description="AI Management Suite background API",
            health_url=_clean_url(settings.aims_health_url),
            operational_url=_clean_url(settings.aims_operational_health_url),
        ),
        ProbeTarget(
            repo="RAMS",
            label="RAMS",
            category="background_api",
            description="Repository Automation Management Service",
            health_url=_clean_url(settings.rams_health_url),
            operational_url=_clean_url(settings.rams_readiness_url),
            operational_token=(settings.rams_health_bearer_token or "").strip(),
        ),
        ProbeTarget(
            repo="MAST",
            label="MAST",
            category="background_api",
            description="Master automation scheduler and trigger service",
            health_url=_clean_url(settings.mast_health_url),
            operational_url=_clean_url(settings.mast_status_url),
        ),
        ProbeTarget(
            repo="IRS",
            label="IRS",
            category="static_service",
            description="Cloudflare image redirect service",
            health_url=_clean_url(settings.irs_health_url),
        ),
        ProbeTarget(
            repo="Website",
            label="Website",
            category="static_service",
            description="Jonathan Harris public website",
            health_url=_clean_url(settings.website_health_url),
        ),
    ]


def _safe_payload(response: httpx.Response) -> dict[str, Any] | None:
    content_type = response.headers.get("content-type", "").lower()
    if "json" not in content_type:
        return None
    try:
        raw = response.json()
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None

    allowed = {
        "ok",
        "status",
        "readiness",
        "service",
        "app",
        "build",
        "env",
        "pipelines",
        "checks",
        "time",
        "schedulerEnabled",
        "scheduler_enabled",
        "running",
        "jobs",
        "lastTickAt",
        "last_tick_at",
    }
    return {key: value for key, value in raw.items() if key in allowed}


async def _probe(
    client: httpx.AsyncClient,
    *,
    url: str,
    token: str = "",
) -> dict[str, Any]:
    if not url:
        return {
            "status": "not_configured",
            "configured": False,
            "http_status": None,
            "latency_ms": None,
            "checked_at": _now_iso(),
            "detail": "Health URL is not configured.",
        }

    headers = {"Accept": "application/json,text/html;q=0.8,*/*;q=0.5"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    started = time.perf_counter()
    try:
        response = await client.get(url, headers=headers, follow_redirects=True)
        latency_ms = round((time.perf_counter() - started) * 1000)
        healthy = 200 <= response.status_code < 400
        return {
            "status": "healthy" if healthy else "down",
            "configured": True,
            "http_status": response.status_code,
            "latency_ms": latency_ms,
            "checked_at": _now_iso(),
            "detail": "Probe returned a successful response." if healthy else f"Probe returned HTTP {response.status_code}.",
            "payload": _safe_payload(response),
        }
    except httpx.TimeoutException:
        return {
            "status": "down",
            "configured": True,
            "http_status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "checked_at": _now_iso(),
            "detail": "Probe timed out.",
        }
    except httpx.HTTPError as exc:
        return {
            "status": "down",
            "configured": True,
            "http_status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "checked_at": _now_iso(),
            "detail": f"Probe failed: {exc.__class__.__name__}.",
        }


def _combine_status(liveness: dict[str, Any], operational: dict[str, Any] | None) -> str:
    if liveness["status"] == "not_configured":
        return "not_configured"
    if liveness["status"] != "healthy":
        return "down"
    if operational and operational["status"] not in {"healthy", "not_configured"}:
        return "degraded"
    if operational and operational["status"] == "not_configured":
        return "degraded"
    return "healthy"


async def _probe_target(client: httpx.AsyncClient, target: ProbeTarget) -> dict[str, Any]:
    liveness_task = _probe(client, url=target.health_url)
    operational_task = (
        _probe(client, url=target.operational_url, token=target.operational_token)
        if target.operational_url
        else None
    )
    if operational_task is None:
        liveness = await liveness_task
        operational = None
    else:
        liveness, operational = await asyncio.gather(liveness_task, operational_task)

    status = _combine_status(liveness, operational)
    detail = liveness.get("detail") or ""
    if status == "degraded" and operational:
        detail = f"Liveness passed; operational check: {operational.get('detail', 'not ready')}"

    return {
        "repo": target.repo,
        "label": target.label,
        "category": target.category,
        "description": target.description,
        "status": status,
        "detail": detail,
        "liveness": liveness,
        "operational": operational,
    }


async def build_repo_health_report(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return bounded, redacted ecosystem health for the Ops dashboard."""

    if not settings.repo_health_enabled:
        return {
            "ok": True,
            "overall_status": "disabled",
            "generated_at": _now_iso(),
            "cache_seconds": settings.repo_health_cache_seconds,
            "summary": {"total": 0, "healthy": 0, "degraded": 0, "down": 0, "not_configured": 0},
            "repos": [],
            "note": "Repository health monitoring is disabled.",
        }

    now = time.monotonic()
    if not force_refresh and _CACHE.get("payload") is not None and now < float(_CACHE.get("expires_at", 0.0)):
        cached = dict(_CACHE["payload"])
        cached["cached"] = True
        return cached

    async with _CACHE_LOCK:
        now = time.monotonic()
        if not force_refresh and _CACHE.get("payload") is not None and now < float(_CACHE.get("expires_at", 0.0)):
            cached = dict(_CACHE["payload"])
            cached["cached"] = True
            return cached

        readiness = build_readiness_report(settings)
        local_status = "healthy" if readiness.ready else "degraded"
        local_item = {
            "repo": "HIVE",
            "label": "HIVE",
            "category": "core_api",
            "description": "HIVE FastAPI backend and operator API",
            "status": local_status,
            "detail": "Backend process is live and production configuration is ready." if readiness.ready else "Backend is live but production readiness has warnings or errors.",
            "liveness": {
                "status": "healthy",
                "configured": True,
                "http_status": 200,
                "latency_ms": 0,
                "checked_at": _now_iso(),
                "detail": "Local process check passed.",
                "payload": {"ok": True, "build": BUILD_STAGE},
            },
            "operational": {
                "status": local_status,
                "configured": True,
                "http_status": 200 if readiness.ready else 503,
                "latency_ms": 0,
                "checked_at": _now_iso(),
                "detail": "Production readiness passed." if readiness.ready else "Production readiness is not fully satisfied.",
                "payload": readiness.public_payload(),
            },
        }

        owns_client = client is None
        active_client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.repo_health_timeout_seconds),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"User-Agent": f"HIVE/{settings.app_version} ecosystem-health"},
        )
        try:
            remote_items = await asyncio.gather(*(_probe_target(active_client, target) for target in _targets(settings)))
        finally:
            if owns_client:
                await active_client.aclose()

        repos = [local_item, *remote_items]
        summary = {
            "total": len(repos),
            "healthy": sum(1 for item in repos if item["status"] == "healthy"),
            "degraded": sum(1 for item in repos if item["status"] == "degraded"),
            "down": sum(1 for item in repos if item["status"] == "down"),
            "not_configured": sum(1 for item in repos if item["status"] == "not_configured"),
        }
        if summary["down"]:
            overall = "down"
        elif summary["degraded"] or summary["not_configured"]:
            overall = "degraded"
        else:
            overall = "healthy"

        payload = {
            "ok": True,
            "overall_status": overall,
            "generated_at": _now_iso(),
            "cache_seconds": settings.repo_health_cache_seconds,
            "cached": False,
            "summary": summary,
            "repos": repos,
            "note": "AIMS and RAMS include background-API operational checks when configured; static repositories use bounded public reachability checks.",
        }
        _CACHE["payload"] = payload
        _CACHE["expires_at"] = time.monotonic() + max(0, settings.repo_health_cache_seconds)
        return payload


def clear_repo_health_cache() -> None:
    _CACHE["payload"] = None
    _CACHE["expires_at"] = 0.0
