from __future__ import annotations

from typing import Any

import httpx

KOYEB_API_BASE = "https://app.koyeb.com/v1/services"


class KoyebControlError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "HIVE/production",
    }


async def service_action(
    client: httpx.AsyncClient,
    *,
    token: str,
    service_id: str,
    action: str,
) -> dict[str, Any]:
    token = token.strip()
    service_id = service_id.strip()
    action = action.strip().lower()
    if action not in {"resume", "pause"}:
        raise KoyebControlError(f"Unsupported Koyeb service action: {action}")
    if not token or not service_id:
        raise KoyebControlError("Koyeb control is not configured for this service.")

    try:
        response = await client.post(
            f"{KOYEB_API_BASE}/{service_id}/{action}",
            headers=_headers(token),
        )
    except httpx.HTTPError as exc:
        raise KoyebControlError(f"Koyeb {action} request failed: {exc.__class__.__name__}.") from exc
    if response.status_code >= 400:
        raise KoyebControlError(f"Koyeb {action} request returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return {"ok": True, "action": action, "service_id": service_id, "response": payload}


async def service_status(
    client: httpx.AsyncClient,
    *,
    token: str,
    service_id: str,
) -> dict[str, Any]:
    token = token.strip()
    service_id = service_id.strip()
    if not token or not service_id:
        return {"configured": False, "status": "not_configured", "detail": "Koyeb service monitoring is not configured."}
    try:
        response = await client.get(f"{KOYEB_API_BASE}/{service_id}", headers=_headers(token))
    except httpx.HTTPError as exc:
        return {"configured": True, "status": "down", "detail": f"Koyeb status request failed: {exc.__class__.__name__}."}
    if response.status_code >= 400:
        return {"configured": True, "status": "down", "http_status": response.status_code, "detail": f"Koyeb status request returned HTTP {response.status_code}."}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    service = payload.get("service") if isinstance(payload, dict) else None
    if not isinstance(service, dict):
        service = payload if isinstance(payload, dict) else {}
    raw = str(service.get("status") or service.get("state") or "").strip().lower()
    mapping = {
        "healthy": "healthy", "running": "healthy", "active": "healthy", "resuming": "starting",
        "starting": "starting", "paused": "standby", "pausing": "standby", "degraded": "degraded",
        "unhealthy": "down", "error": "down", "failed": "down",
    }
    return {
        "configured": True,
        "status": mapping.get(raw, "healthy" if raw else "degraded"),
        "provider_status": raw or None,
        "http_status": response.status_code,
        "detail": f"Koyeb service state: {raw or 'unknown'}.",
    }
