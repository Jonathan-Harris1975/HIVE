from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.production import build_readiness_report
from app.core.version import BUILD_STAGE
from app.storage.r2 import R2Storage

logger = logging.getLogger("uvicorn.error.hive.repo_health")


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
            operational_token=(
                settings.rams_readiness_bearer_token
                or settings.rams_health_bearer_token
                or ""
            ).strip(),
        ),
        ProbeTarget(
            repo="MAST",
            label="MAST",
            category="background_api",
            description="Master automation scheduler worker",
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
        "ready",
        "healthy",
        "status",
        "state",
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


def _normalise_probe_value(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _payload_status(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None

    if payload.get("ok") is False or payload.get("healthy") is False or payload.get("ready") is False:
        return "degraded"

    candidates = (payload.get("readiness"), payload.get("status"), payload.get("state"))
    for candidate in candidates:
        value = _normalise_probe_value(candidate)
        if not value:
            continue
        if value in {"ok", "ready", "healthy", "live", "online", "active", "running", "up"}:
            return "healthy"
        if value in {"blocked", "auth_blocked", "forbidden", "unauthorised", "unauthorized"}:
            return "blocked"
        if value in {"degraded", "partial", "warning", "warn", "stale", "not_ready", "unready"}:
            return "degraded"
        if value in {"down", "offline", "failed", "failure", "error", "critical"}:
            return "down"

    if payload.get("ok") is True or payload.get("healthy") is True or payload.get("ready") is True:
        return "healthy"
    return None


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
        payload = _safe_payload(response)
        payload_status = _payload_status(payload)
        if 200 <= response.status_code < 400:
            status = payload_status or "healthy"
            detail = "Probe returned a successful response."
            if status == "degraded":
                detail = "Probe responded but reported degraded or not-ready payload state."
            elif status == "blocked":
                detail = "Probe responded but reported a blocked payload state."
            elif status == "down":
                detail = "Probe responded but reported a down payload state."
        elif response.status_code in {401, 403}:
            status = "blocked"
            detail = f"Probe was blocked by authentication or authorisation (HTTP {response.status_code})."
        elif response.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
            status = "degraded"
            detail = f"Probe returned transient or degraded HTTP {response.status_code}."
        else:
            status = "down"
            detail = f"Probe returned HTTP {response.status_code}."
        return {
            "status": status,
            "configured": True,
            "http_status": response.status_code,
            "latency_ms": latency_ms,
            "checked_at": _now_iso(),
            "detail": detail,
            "payload": payload,
        }
    except httpx.TimeoutException:
        logger.warning("Repo health probe timed out url=%s", url)
        return {
            "status": "down",
            "configured": True,
            "http_status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "checked_at": _now_iso(),
            "detail": "Probe timed out.",
        }
    except httpx.HTTPError as exc:
        logger.warning(
            "Repo health probe failed url=%s error_type=%s error=%s", url, type(exc).__name__, exc
        )
        return {
            "status": "down",
            "configured": True,
            "http_status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "checked_at": _now_iso(),
            "detail": f"Probe failed: {exc.__class__.__name__}.",
        }


def _combine_status(liveness: dict[str, Any], operational: dict[str, Any] | None) -> str:
    liveness_status = str(liveness.get("status") or "not_configured")
    operational_status = str((operational or {}).get("status") or "")
    if liveness_status == "not_configured":
        return "not_configured"
    if liveness_status in {"blocked", "degraded"}:
        return "degraded"
    if liveness_status != "healthy":
        return "down"
    if operational and operational_status not in {"healthy", "not_configured"}:
        return "degraded"
    if operational and operational_status == "not_configured":
        return "degraded"
    return "healthy"



def _readiness_probe(operational: dict[str, Any] | None, status: str) -> dict[str, Any]:
    source = operational or {}
    source_status = str(source.get("status") or status)
    if source_status == "healthy":
        readiness_status = "ready"
    elif source_status in {"blocked", "auth_blocked", "forbidden", "unauthorised", "unauthorized"}:
        readiness_status = "blocked"
    elif source_status in {"down", "failed", "error"}:
        readiness_status = "not_ready"
    elif source_status in {"standby", "maintenance", "starting"}:
        # Intentional, non-fault states: surfaced verbatim rather than folded into
        # "not_ready" so the UI can distinguish "asleep on purpose" from "broken".
        readiness_status = source_status
    else:
        readiness_status = "partial"
    return {
        "status": readiness_status,
        "configured": bool(source.get("configured", status != "not_configured")),
        "http_status": source.get("http_status"),
        "latency_ms": source.get("latency_ms"),
        "checked_at": source.get("checked_at") or _now_iso(),
        "detail": source.get("detail") or f"Readiness derived from operational status: {source_status}.",
        "payload": source.get("payload"),
    }


def _with_readiness(item: dict[str, Any]) -> dict[str, Any]:
    if item.get("readiness") is not None:
        return item
    return {**item, "readiness": _readiness_probe(item.get("operational"), str(item.get("status") or "not_configured"))}

_INTENTIONAL_LIFECYCLE_STATES = {"standby", "maintenance"}


async def _probe_target(
    client: httpx.AsyncClient,
    target: ProbeTarget,
    *,
    lifecycle_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # MAST's lifecycle ledger is authoritative for AIMS/RAMS while paused: a paused
    # Koyeb instance cannot answer any network probe at all, so a failed probe here
    # would otherwise be indistinguishable from a genuine outage. Trust the ledger
    # instead of probing when it reports an intentional non-running state.
    hint_state = str((lifecycle_hint or {}).get("state") or "")
    if hint_state in _INTENTIONAL_LIFECYCLE_STATES:
        reason = (lifecycle_hint or {}).get("reason") or "n/a"
        since = (lifecycle_hint or {}).get("since")
        detail = f"MAST reports this service is intentionally in {hint_state} (reason: {reason})."
        probe = {
            "status": hint_state,
            "configured": True,
            "http_status": None,
            "latency_ms": None,
            "checked_at": _now_iso(),
            "detail": detail,
            "payload": {"source": "mast_lifecycle_ledger", "since": since, "reason": reason},
        }
        return _with_readiness({
            "repo": target.repo,
            "label": target.label,
            "category": target.category,
            "description": target.description,
            "status": hint_state,
            "detail": detail,
            "liveness": probe,
            "operational": probe,
        })

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

    # If MAST believes it just told this service to resume, a still-failing network
    # probe means "starting up", not "down" - avoid flapping the dashboard to a fault
    # colour during a normal, expected wake-up window.
    if status == "down" and hint_state == "starting":
        status = "starting"
        detail = f"MAST is resuming this service (starting since {(lifecycle_hint or {}).get('since') or 'recently'})."

    return _with_readiness({
        "repo": target.repo,
        "label": target.label,
        "category": target.category,
        "description": target.description,
        "status": status,
        "detail": detail,
        "liveness": liveness,
        "operational": operational,
    })


def _parse_utc_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _mast_monitor_mode(settings: Settings, target: ProbeTarget) -> str:
    mode = settings.mast_monitor_mode
    if mode != "auto":
        return mode
    if target.health_url or target.operational_url:
        return "http"
    lane = settings.internal_r2_lane(settings.mast_state_r2_lane)
    if lane and (lane.get("readable") or lane.get("public_base_url")):
        return "r2"
    return "disabled"


async def _read_public_object(
    client: httpx.AsyncClient,
    *,
    url: str,
    max_bytes: int,
) -> tuple[bytes | None, int | None, str | None]:
    separator = "&" if "?" in url else "?"
    cache_busted_url = f"{url}{separator}hive-health={int(time.time())}"
    try:
        async with client.stream(
            "GET",
            cache_busted_url,
            headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            follow_redirects=True,
        ) as response:
            if not 200 <= response.status_code < 400:
                return None, response.status_code, f"Public state returned HTTP {response.status_code}."
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    return None, response.status_code, "Public state exceeded the configured size limit."
            return bytes(body), response.status_code, None
    except httpx.TimeoutException:
        logger.warning("MAST public-state probe timed out url=%s", url)
        return None, None, "Public state probe timed out."
    except httpx.HTTPError as exc:
        logger.warning(
            "MAST public-state probe failed url=%s error_type=%s error=%s", url, type(exc).__name__, exc
        )
        return None, None, f"Public state probe failed: {exc.__class__.__name__}."


def _mast_state_summary(payload: dict[str, Any]) -> dict[str, Any]:
    recent = payload.get("recentResults")
    recent_results = recent if isinstance(recent, list) else []
    bounded = [item for item in recent_results[:10] if isinstance(item, dict)]
    failures = sum(1 for item in bounded if item.get("ok") is False)
    latest = bounded[0] if bounded else {}
    return {
        "started_at": payload.get("startedAt") or payload.get("started_at"),
        "last_tick_at": payload.get("lastTickAt") or payload.get("last_tick_at"),
        "recent_results_checked": len(bounded),
        "recent_failures": failures,
        "latest_result_ok": latest.get("ok") if latest else None,
        "latest_result_finished_at": (
            (latest.get("finishedAt") or latest.get("finished_at")) if latest else None
        ),
    }


async def _fetch_mast_service_ledger(
    client: httpx.AsyncClient, *, settings: Settings
) -> dict[str, Any] | None:
    """Best-effort read of MAST's durable AIMS/RAMS lifecycle ledger.

    MAST is the only actor able to distinguish an intentional Standby (it paused the
    service via Koyeb) from a genuine outage, since a paused instance cannot answer
    any probe of its own. A miss here (unreadable/not configured) simply means
    AIMS/RAMS fall back to ordinary network probing.
    """
    lane = settings.internal_r2_lane(settings.mast_state_r2_lane)
    key = settings.mast_state_object_key.strip().lstrip("/")
    public_url = settings.internal_public_url_for_r2_lane(settings.mast_state_r2_lane, key)
    if not (lane and key and (lane.get("readable") or public_url)):
        return None

    raw: bytes | None = None
    if lane.get("readable") and lane.get("bucket"):
        storage = R2Storage(settings)
        try:
            obj = await asyncio.wait_for(
                asyncio.to_thread(
                    storage.read_object,
                    key,
                    settings.mast_state_max_bytes,
                    bucket=str(lane["bucket"]),
                    public_base_url=lane.get("public_base_url"),
                    read_only=not bool(lane.get("writable")),
                ),
                timeout=settings.repo_health_timeout_seconds,
            )
            raw = obj.content
        except Exception as error:  # noqa: BLE001 - any failure here must fall back to the public URL, not crash
            logger.warning(
                "MAST state R2 read failed lane=%s key=%s error_type=%s error=%s",
                settings.mast_state_r2_lane,
                key,
                type(error).__name__,
                error,
            )
            raw = None

    if raw is None and public_url:
        raw, _http_status, _error = await _read_public_object(
            client, url=public_url, max_bytes=settings.mast_state_max_bytes,
        )

    if raw is None:
        return None

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        logger.warning(
            "MAST state payload could not be decoded lane=%s error_type=%s error=%s",
            settings.mast_state_r2_lane,
            type(error).__name__,
            error,
        )
        return None
    if not isinstance(decoded, dict):
        return None

    services = decoded.get("services")
    return services if isinstance(services, dict) else None


async def _probe_mast_worker(
    client: httpx.AsyncClient,
    *,
    settings: Settings,
    target: ProbeTarget,
) -> dict[str, Any]:
    mode = _mast_monitor_mode(settings, target)
    if mode == "http":
        return await _probe_target(client, target)
    if mode == "disabled":
        not_configured = {
            "status": "not_configured",
            "configured": False,
            "http_status": None,
            "latency_ms": None,
            "checked_at": _now_iso(),
            "detail": "MAST worker monitoring is disabled or not configured.",
        }
        return {
            "repo": target.repo,
            "label": target.label,
            "category": target.category,
            "description": target.description,
            "status": "not_configured",
            "detail": not_configured["detail"],
            "liveness": not_configured,
            "operational": None,
        }

    lane = settings.internal_r2_lane(settings.mast_state_r2_lane)
    key = settings.mast_state_object_key.strip().lstrip("/")
    public_url = settings.internal_public_url_for_r2_lane(settings.mast_state_r2_lane, key)
    configured = bool(lane and key and (lane.get("readable") or public_url))
    if not configured:
        not_configured = {
            "status": "not_configured",
            "configured": False,
            "http_status": None,
            "latency_ms": None,
            "checked_at": _now_iso(),
            "detail": "MAST durable-state lane or object key is not configured.",
        }
        return {
            "repo": target.repo,
            "label": target.label,
            "category": target.category,
            "description": target.description,
            "status": "not_configured",
            "detail": not_configured["detail"],
            "liveness": not_configured,
            "operational": None,
        }

    started = time.perf_counter()
    raw: bytes | None = None
    source: str | None = None
    http_status: int | None = None
    errors: list[str] = []

    if lane and lane.get("readable") and lane.get("bucket"):
        storage = R2Storage(settings)
        try:
            obj = await asyncio.wait_for(
                asyncio.to_thread(
                    storage.read_object,
                    key,
                    settings.mast_state_max_bytes,
                    bucket=str(lane["bucket"]),
                    public_base_url=lane.get("public_base_url"),
                    read_only=not bool(lane.get("writable")),
                ),
                timeout=settings.repo_health_timeout_seconds,
            )
            raw = obj.content
            source = "r2_s3"
        except TimeoutError:
            errors.append("Scoped R2 state probe timed out.")
        except Exception as exc:  # provider errors are intentionally redacted
            errors.append(f"Scoped R2 state probe failed: {exc.__class__.__name__}.")

    if raw is None and public_url:
        raw, http_status, public_error = await _read_public_object(
            client,
            url=public_url,
            max_bytes=settings.mast_state_max_bytes,
        )
        if raw is not None:
            source = "r2_public"
        elif public_error:
            errors.append(public_error)

    latency_ms = round((time.perf_counter() - started) * 1000)
    if raw is None:
        detail = " ".join(errors) or "MAST durable worker state could not be read."
        liveness = {
            "status": "down",
            "configured": True,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "checked_at": _now_iso(),
            "detail": detail,
            "payload": {"source": "durable_r2_state", "lane": settings.mast_state_r2_lane},
        }
        return {
            "repo": target.repo,
            "label": target.label,
            "category": target.category,
            "description": target.description,
            "status": "down",
            "detail": detail,
            "liveness": liveness,
            "operational": None,
        }

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        logger.warning(
            "MAST durable worker state could not be decoded lane=%s error_type=%s error=%s",
            settings.mast_state_r2_lane,
            type(error).__name__,
            error,
        )
        decoded = None
    if not isinstance(decoded, dict):
        detail = "MAST durable worker state is not valid JSON object data."
        liveness = {
            "status": "degraded",
            "configured": True,
            "http_status": http_status or 200,
            "latency_ms": latency_ms,
            "checked_at": _now_iso(),
            "detail": detail,
            "payload": {"source": source, "lane": settings.mast_state_r2_lane},
        }
        return {
            "repo": target.repo,
            "label": target.label,
            "category": target.category,
            "description": target.description,
            "status": "degraded",
            "detail": detail,
            "liveness": liveness,
            "operational": liveness,
        }

    summary = _mast_state_summary(decoded)
    last_tick = _parse_utc_timestamp(summary["last_tick_at"])
    if last_tick is None:
        status = "degraded"
        age_seconds = None
        detail = "MAST durable state is readable but does not contain a valid worker heartbeat."
    else:
        age_seconds = max(0, int((datetime.now(UTC) - last_tick).total_seconds()))
        healthy_limit = settings.mast_state_healthy_max_age_seconds
        down_limit = max(healthy_limit, settings.mast_state_down_max_age_seconds)
        if age_seconds <= healthy_limit:
            status = "healthy"
            detail = f"MAST worker heartbeat updated {age_seconds} seconds ago."
        elif age_seconds <= down_limit:
            status = "degraded"
            detail = f"MAST worker heartbeat is stale ({age_seconds} seconds old)."
        else:
            status = "down"
            detail = f"MAST worker heartbeat has stopped ({age_seconds} seconds old)."

    payload = {
        **summary,
        "heartbeat_age_seconds": age_seconds,
        "healthy_max_age_seconds": settings.mast_state_healthy_max_age_seconds,
        "down_max_age_seconds": settings.mast_state_down_max_age_seconds,
        "source": source,
        "lane": settings.mast_state_r2_lane,
        "object_key": key,
    }
    probe = {
        "status": status,
        "configured": True,
        "http_status": http_status or 200,
        "latency_ms": latency_ms,
        "checked_at": _now_iso(),
        "detail": detail,
        "payload": payload,
    }
    return {
        "repo": target.repo,
        "label": target.label,
        "category": target.category,
        "description": target.description,
        "status": status,
        "detail": detail,
        "liveness": probe,
        "operational": probe,
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
            service_ledger = await _fetch_mast_service_ledger(active_client, settings=settings)
            tasks = [
                (
                    _probe_mast_worker(active_client, settings=settings, target=target)
                    if target.repo == "MAST"
                    else _probe_target(
                        active_client,
                        target,
                        lifecycle_hint=(service_ledger or {}).get(target.repo.lower())
                        if target.repo in {"AIMS", "RAMS"}
                        else None,
                    )
                )
                for target in _targets(settings)
            ]
            remote_items = await asyncio.gather(*tasks)
        finally:
            if owns_client:
                await active_client.aclose()

        repos = [_with_readiness(local_item), *[_with_readiness(item) for item in remote_items]]
        summary = {
            "total": len(repos),
            "healthy": sum(1 for item in repos if item["status"] == "healthy"),
            "degraded": sum(1 for item in repos if item["status"] == "degraded"),
            "down": sum(1 for item in repos if item["status"] == "down"),
            "not_configured": sum(1 for item in repos if item["status"] == "not_configured"),
            "standby": sum(1 for item in repos if item["status"] in {"standby", "maintenance"}),
            "starting": sum(1 for item in repos if item["status"] == "starting"),
        }
        if summary["down"]:
            overall = "down"
        elif summary["degraded"] or summary["not_configured"] or summary["starting"]:
            overall = "degraded"
        else:
            # Standby/Maintenance are intentional operational states, not faults: they
            # never push the ecosystem overview into "down" or "degraded" on their own.
            overall = "healthy"

        payload = {
            "ok": True,
            "overall_status": overall,
            "generated_at": _now_iso(),
            "cache_seconds": settings.repo_health_cache_seconds,
            "cached": False,
            "summary": summary,
            "repos": repos,
            "note": (
                "AIMS and RAMS include background-API operational checks; MAST uses its "
                "durable R2 worker heartbeat when configured; static repositories use "
                "bounded public reachability checks."
            ),
        }
        _CACHE["payload"] = payload
        _CACHE["expires_at"] = time.monotonic() + max(0, settings.repo_health_cache_seconds)
        return payload


def clear_repo_health_cache() -> None:
    _CACHE["payload"] = None
    _CACHE["expires_at"] = 0.0
