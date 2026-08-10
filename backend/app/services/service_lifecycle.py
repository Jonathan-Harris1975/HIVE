"""Transparent wake-up orchestration for lifecycle-aware services (AIMS, RAMS).

When a user (or an internal HIVE workflow) needs functionality from a service that is
currently in Standby, HIVE should not surface an error and stop - it should:

  1. Ask MAST to resume the service (MAST owns the Koyeb power management call).
  2. Poll the service's own health endpoint until it reports ready.
  3. Let the caller proceed with the original request once ready.

MAST's on-demand resume endpoint is idempotent (a resume request against an
already-starting/online service is a no-op that returns the current ledger entry), so
this module can call it unconditionally rather than trying to out-guess MAST's state.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

import httpx

from app.core.config import Settings
from app.storage.r2 import R2Storage

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


class MastResumeError(ServiceLifecycleError):
    pass


@dataclass
class WakeResult:
    repo: str
    ready: bool
    already_online: bool
    attempts: list[dict[str, Any]] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    mast_resume_response: dict[str, Any] | None = None


def _health_url_for(settings: Settings, repo: str) -> str:
    if repo.upper() == "AIMS":
        return (settings.aims_health_url or "").strip()
    if repo.upper() == "RAMS":
        return (settings.rams_health_url or "").strip()
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


async def _queue_mast_resume_via_r2(settings: Settings, repo: str) -> dict[str, Any]:
    """Queue a wake request for the MAST Worker through its hidden R2 control object.

    MAST is normally deployed as a Koyeb Worker and therefore has no public HTTP
    ingress.  The Worker already polls its durable operator-control object on each
    scheduler tick, so this channel keeps power-management ownership in MAST without
    exposing the internal ``meta_system`` bucket in HIVE-UI.
    """
    service_key = MANAGED_SERVICES.get(repo.upper())
    if not service_key:
        raise UnknownServiceError(f"Unknown service: {repo}")

    lane = settings.internal_r2_lane(settings.mast_state_r2_lane)
    key = settings.mast_operator_control_object_key.strip().lstrip("/")
    if not lane or not lane.get("bucket") or not lane.get("writable") or not key:
        raise MastResumeError(
            "MAST Worker control is not writable. Configure the hidden MAST R2 lane "
            "and multi-bucket write credentials; MAST_BASE_URL is only needed for an HTTP deployment."
        )

    storage = R2Storage(settings)
    bucket = str(lane["bucket"])
    payload: dict[str, Any] = {}
    try:
        existing = await asyncio.to_thread(
            storage.read_object,
            key,
            settings.mast_state_max_bytes,
            bucket=bucket,
            public_base_url=lane.get("public_base_url"),
            read_only=False,
        )
        decoded = json.loads(existing.content.decode("utf-8"))
        if isinstance(decoded, dict):
            payload = decoded
    except FileNotFoundError:
        payload = {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    except Exception as exc:
        raise MastResumeError(
            f"Could not read MAST Worker control state: {exc.__class__.__name__}."
        ) from exc

    now = datetime.now(UTC)
    commands = [item for item in payload.get("commands", []) if isinstance(item, dict)]
    # Avoid stacking duplicate requests while the Worker is between scheduler ticks.
    for item in reversed(commands[-20:]):
        if item.get("type") != "service.resume" or item.get("service") != service_key:
            continue
        requested_at = str(item.get("requestedAt") or "")
        try:
            requested = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
            if requested.tzinfo is None:
                requested = requested.replace(tzinfo=UTC)
            if (now - requested.astimezone(UTC)).total_seconds() < 60:
                return {
                    "ok": True,
                    "service": service_key,
                    "idempotent": True,
                    "queued": True,
                    "transport": "r2_worker_control",
                    "command_id": item.get("id"),
                }
        except ValueError:
            pass

    command_id = uuid.uuid4().hex
    commands.append({
        "id": command_id,
        "type": "service.resume",
        "service": service_key,
        "reason": "hive-user-request",
        "source": "HIVE",
        "requestedAt": now.isoformat(),
    })
    payload["commands"] = commands[-50:]
    payload.setdefault("schedulerEnabled", True)
    payload.setdefault("maintenanceMode", False)
    payload["updatedAt"] = now.isoformat()

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > settings.mast_state_max_bytes:
        raise MastResumeError("MAST Worker control object would exceed the configured size limit.")
    try:
        await asyncio.to_thread(
            storage.client(read_only=False).put_object,
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            CacheControl="no-store",
        )
    except Exception as exc:
        raise MastResumeError(
            f"Could not queue MAST Worker resume request: {exc.__class__.__name__}."
        ) from exc

    return {
        "ok": True,
        "service": service_key,
        "idempotent": False,
        "queued": True,
        "transport": "r2_worker_control",
        "command_id": command_id,
    }


async def request_mast_resume(
    client: httpx.AsyncClient, settings: Settings, repo: str
) -> dict[str, Any]:
    """Ask MAST to resume a managed service.

    Worker deployments use R2 operator-control commands.  The legacy HTTP route is
    retained for deployments that deliberately expose MAST as a web service.
    """
    service_key = MANAGED_SERVICES.get(repo.upper())
    if not service_key:
        raise UnknownServiceError(f"Unknown service: {repo}")

    base_url = (settings.mast_base_url or "").strip().rstrip("/")
    if not base_url:
        return await _queue_mast_resume_via_r2(settings, repo)

    headers = {"Accept": "application/json"}
    if settings.mast_admin_token:
        headers["Authorization"] = f"Bearer {settings.mast_admin_token}"

    try:
        response = await client.post(
            f"{base_url}/services/{service_key}/resume",
            headers=headers,
            json={"reason": "hive-user-request"},
        )
    except httpx.HTTPError as exc:
        raise MastResumeError(f"Could not reach MAST to request resume: {exc.__class__.__name__}.") from exc

    try:
        response_payload = response.json()
    except ValueError:
        response_payload = {"ok": response.status_code < 400}

    if response.status_code >= 500 or (response.status_code >= 400 and not response_payload.get("idempotent")):
        raise MastResumeError(
            f"MAST refused the resume request for {repo} (HTTP {response.status_code}): "
            f"{response_payload.get('error') or response_payload.get('detail') or 'unknown error'}."
        )

    return response_payload


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
    `ServiceWakeTimeout` or `MastResumeError` on failure; the caller decides how to
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
            mast_response = await request_mast_resume(active_client, settings, repo_upper)
        except MastResumeError as exc:
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
