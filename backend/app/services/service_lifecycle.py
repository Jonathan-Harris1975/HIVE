"""Transparent Koyeb wake-up orchestration for lifecycle-aware services.

HIVE controls AIMS and RAMS directly through Koyeb's service API, then polls each
service's own health endpoint until it is ready. MAST remains the independent
scheduler Worker and is not used as an HTTP/R2 command relay.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from app.core.config import Settings
from app.services.koyeb_control import KoyebControlError, service_action

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]] | None

MANAGED_SERVICES = {
    "AIMS": "aims",
    "RAMS": "rams",
}


class ServiceLifecycleError(RuntimeError):
    """Base error for wake-up orchestration failures."""


class UnknownServiceError(ServiceLifecycleError):
    pass


class ServiceWakeTimeout(ServiceLifecycleError):
    def __init__(self, repo: str, elapsed_seconds: float, attempts: int):
        super().__init__(
            f"{repo} did not become healthy within {elapsed_seconds:.0f}s ({attempts} attempts)."
        )
        self.repo = repo
        self.elapsed_seconds = elapsed_seconds
        self.attempts = attempts


class ServiceResumeError(ServiceLifecycleError):
    pass


@dataclass
class WakeResult:
    repo: str
    ready: bool
    already_online: bool
    attempts: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    mast_resume_response: dict[str, Any] | None = None  # backwards-compatible payload field


def _health_url_for(settings: Settings, repo: str) -> str:
    if repo.upper() == "AIMS":
        return (settings.aims_health_url or "").strip()
    if repo.upper() == "RAMS":
        return (settings.rams_health_url or "").strip()
    raise UnknownServiceError(f"Unknown service: {repo}")


def _service_id_for(settings: Settings, repo: str) -> str:
    if repo.upper() == "AIMS":
        return settings.koyeb_service_id_aims.strip()
    if repo.upper() == "RAMS":
        return settings.koyeb_service_id_rams.strip()
    raise UnknownServiceError(f"Unknown service: {repo}")


async def _probe_healthy(client: httpx.AsyncClient, url: str) -> tuple[bool, dict[str, Any]]:
    if not url:
        return False, {"ok": False, "reason": "health-url-not-configured"}
    started = time.perf_counter()
    try:
        response = await client.get(url, headers={"Accept": "application/json"}, follow_redirects=True)
        latency_ms = round((time.perf_counter() - started) * 1000)
        return response.status_code < 400, {"ok": response.status_code < 400, "http_status": response.status_code, "latency_ms": latency_ms}
    except httpx.HTTPError as exc:
        latency_ms = round((time.perf_counter() - started) * 1000)
        return False, {"ok": False, "reason": exc.__class__.__name__, "latency_ms": latency_ms}


async def request_service_resume(
    client: httpx.AsyncClient, settings: Settings, repo: str
) -> dict[str, Any]:
    """Resume AIMS/RAMS directly through Koyeb's control plane."""
    service_key = MANAGED_SERVICES.get(repo.upper())
    if not service_key:
        raise UnknownServiceError(f"Unknown service: {repo}")
    service_id = _service_id_for(settings, repo)
    if not settings.koyeb_token.strip() or not service_id:
        raise ServiceResumeError(
            f"Koyeb wake control for {repo} is not configured. Set KOYEB_TOKEN and "
            f"KOYEB_SERVICE_ID_{repo.upper()} in HIVE."
        )
    try:
        result = await service_action(
            client, token=settings.koyeb_token, service_id=service_id, action="resume"
        )
    except KoyebControlError as exc:
        raise ServiceResumeError(str(exc)) from exc
    return {**result, "service": service_key, "transport": "koyeb_api"}


async def ensure_service_ready(
    settings: Settings,
    repo: str,
    *,
    client: httpx.AsyncClient | None = None,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float | None = None,
    on_progress: ProgressCallback = None,
) -> WakeResult:
    """Wake `repo` (AIMS/RAMS) if needed and wait until its health endpoint is ready.

    Transparent to the caller: this is the single entry point other HIVE code should
    use before proxying a request to a lifecycle-aware service. Raises
    `ServiceWakeTimeout` or `ServiceResumeError` on failure; the caller decides how to
    surface that (e.g. HTTP 503 with a retry hint).
    """
    repo_upper = repo.upper()
    if repo_upper not in MANAGED_SERVICES:
        raise UnknownServiceError(f"Unknown service: {repo}")

    timeout = timeout_seconds if timeout_seconds is not None else settings.service_wake_timeout_seconds
    interval = poll_interval_seconds if poll_interval_seconds is not None else settings.service_wake_poll_interval_seconds
    health_url = _health_url_for(settings, repo_upper)

    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    started = time.monotonic()
    attempts: list[dict[str, Any]] = []

    async def emit(event: dict[str, Any]) -> None:
        if on_progress is not None:
            await on_progress(event)

    try:
        healthy, probe = await _probe_healthy(active_client, health_url)
        attempts.append({"attempt": 0, "phase": "initial-check", **probe})
        if healthy:
            await emit({"phase": "already-online", "repo": repo_upper})
            return WakeResult(repo=repo_upper, ready=True, already_online=True, attempts=attempts, elapsed_seconds=0.0)

        await emit({"phase": "requesting-resume", "repo": repo_upper})
        try:
            mast_response = await request_service_resume(active_client, settings, repo_upper)
        except ServiceResumeError as exc:
            await emit({"phase": "resume-request-failed", "repo": repo_upper, "error": str(exc)})
            raise

        await emit({"phase": "starting", "repo": repo_upper, "mast_response": mast_response})

        attempt = 0
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= timeout:
                await emit({"phase": "timeout", "repo": repo_upper, "elapsed_seconds": elapsed, "attempts": attempt})
                raise ServiceWakeTimeout(repo_upper, elapsed, attempt)

            await asyncio.sleep(interval)
            attempt += 1
            healthy, probe = await _probe_healthy(active_client, health_url)
            attempts.append({"attempt": attempt, "phase": "poll", **probe})
            await emit({
                "phase": "polling",
                "repo": repo_upper,
                "attempt": attempt,
                "elapsed_seconds": round(time.monotonic() - started, 1),
                "healthy": healthy,
            })
            if healthy:
                total_elapsed = time.monotonic() - started
                await emit({"phase": "ready", "repo": repo_upper, "elapsed_seconds": round(total_elapsed, 1)})
                return WakeResult(
                    repo=repo_upper,
                    ready=True,
                    already_online=False,
                    attempts=attempts,
                    elapsed_seconds=total_elapsed,
                    mast_resume_response=mast_response,
                )
    finally:
        if owns_client:
            await active_client.aclose()
