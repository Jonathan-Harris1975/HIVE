from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.security import require_admin
from app.services.repo_health import clear_repo_health_cache
from app.services.service_lifecycle import (
    MANAGED_SERVICES,
    MastResumeError,
    ServiceWakeTimeout,
    UnknownServiceError,
    ensure_service_ready,
)

router = APIRouter(prefix="/services", tags=["services"], dependencies=[Depends(require_admin)])

ServiceRepo = Literal["AIMS", "RAMS"]

# In-memory wake tickets. HIVE runs as a single Koyeb instance per the existing
# architecture (see AIMS/RAMS themselves), so process-local state is consistent with
# how the rest of the codebase tracks short-lived operational state. Tickets are
# bounded and swept on access so this cannot grow unbounded.
_WAKE_TICKETS: dict[str, dict[str, Any]] = {}
_TICKET_TTL_SECONDS = 3600


def _sweep_tickets() -> None:
    cutoff = time.monotonic() - _TICKET_TTL_SECONDS
    stale = [ticket_id for ticket_id, entry in _WAKE_TICKETS.items() if entry.get("_created_monotonic", 0) < cutoff]
    for ticket_id in stale:
        _WAKE_TICKETS.pop(ticket_id, None)


class EnsureReadyRequest(BaseModel):
    timeout_seconds: int | None = None


async def _run_wake_ticket(ticket_id: str, repo: str, settings: Settings) -> None:
    ticket = _WAKE_TICKETS[ticket_id]

    async def on_progress(event: dict[str, Any]) -> None:
        ticket["events"].append(event)
        ticket["phase"] = event.get("phase", ticket["phase"])

    try:
        result = await ensure_service_ready(settings, repo, on_progress=on_progress)
        ticket["status"] = "ready"
        ticket["result"] = {
            "repo": result.repo,
            "ready": result.ready,
            "already_online": result.already_online,
            "elapsed_seconds": round(result.elapsed_seconds, 1),
            "attempts": len(result.attempts),
        }
        clear_repo_health_cache()
    except ServiceWakeTimeout as exc:
        ticket["status"] = "timeout"
        ticket["error"] = str(exc)
    except (MastResumeError, UnknownServiceError) as exc:
        ticket["status"] = "failed"
        ticket["error"] = str(exc)
    except Exception as exc:  # defensive: never leave a ticket stuck "running"
        ticket["status"] = "failed"
        ticket["error"] = f"Unexpected error: {exc.__class__.__name__}."
    finally:
        ticket["finished_at"] = time.time()


@router.post("/{repo}/ensure-ready", status_code=202)
async def start_ensure_ready(
    repo: ServiceRepo = Path(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Start (or report) a transparent wake-up for a standby AIMS/RAMS.

    HIVE UI calls this when a user action needs a service that health checks show as
    Standby/Starting/unreachable, then polls the returned ticket for progress exactly
    like a startup progress bar.
    """
    if repo not in MANAGED_SERVICES:
        raise HTTPException(status_code=404, detail=f"Unknown service: {repo}")

    _sweep_tickets()
    ticket_id = uuid.uuid4().hex
    _WAKE_TICKETS[ticket_id] = {
        "ticket_id": ticket_id,
        "repo": repo,
        "status": "running",
        "phase": "queued",
        "events": [],
        "result": None,
        "error": None,
        "started_at": time.time(),
        "finished_at": None,
        "_created_monotonic": time.monotonic(),
    }

    asyncio.create_task(_run_wake_ticket(ticket_id, repo, settings))

    return {
        "ok": True,
        "wake_id": ticket_id,
        "repo": repo,
        "status": "running",
        "poll_url": f"/services/{repo}/ensure-ready/{ticket_id}",
    }


@router.get("/{repo}/ensure-ready/{wake_id}")
async def get_ensure_ready_status(
    repo: ServiceRepo = Path(...),
    wake_id: str = Path(...),
) -> dict[str, Any]:
    """Poll progress for a previously started wake-up ticket."""
    ticket = _WAKE_TICKETS.get(wake_id)
    if ticket is None or ticket["repo"] != repo:
        raise HTTPException(status_code=404, detail="Unknown or expired wake ticket.")
    return {k: v for k, v in ticket.items() if not k.startswith("_")}


class ProxyRequest(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    path: str
    json_body: dict[str, Any] | None = None
    timeout_seconds: int | None = None


@router.post("/{repo}/proxy")
async def ensure_ready_and_proxy(
    body: ProxyRequest,
    repo: ServiceRepo = Path(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Transparently wake `repo` if needed, then execute and return the original request.

    This is the generic building block for "user requests functionality requiring a
    standby service": it ensures readiness (requesting a MAST resume and polling health
    if the service is asleep), executes the forwarded call, and returns the result in a
    single round trip - no separate user interaction needed. Callers with tight latency
    budgets should instead use /ensure-ready + their own follow-up call, since this
    endpoint blocks for the full wake duration when the target is in Standby.
    """
    if repo not in MANAGED_SERVICES:
        raise HTTPException(status_code=404, detail=f"Unknown service: {repo}")

    base_url = (settings.aims_health_url if repo == "AIMS" else settings.rams_health_url).strip()
    if not base_url:
        raise HTTPException(status_code=503, detail=f"{repo} base URL is not configured.")
    # Health URLs point at a liveness path (e.g. /health, /livez); derive the service
    # origin from it rather than requiring a second setting.
    origin = base_url.split("://", 1)
    scheme_host = f"{origin[0]}://{origin[1].split('/', 1)[0]}" if len(origin) == 2 else base_url

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        try:
            await ensure_service_ready(
                settings, repo, client=client, timeout_seconds=body.timeout_seconds
            )
        except ServiceWakeTimeout as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (MastResumeError, UnknownServiceError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        clear_repo_health_cache()

        target_url = f"{scheme_host}{body.path if body.path.startswith('/') else '/' + body.path}"
        try:
            response = await client.request(body.method, target_url, json=body.json_body)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Request to {repo} failed: {exc.__class__.__name__}.") from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:2000]}

    return {"ok": response.status_code < 400, "http_status": response.status_code, "body": payload}
