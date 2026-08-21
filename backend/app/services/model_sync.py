"""Closed-loop propagation of HIVE AI Council model defaults to AIMS and RAMS."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from app.core.config import Settings
from app.services.koyeb_control import KoyebControlError, service_action
from app.services.service_lifecycle import ServiceLifecycleError, WakeResult, ensure_service_ready

_TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504}


class ModelSyncError(RuntimeError):
    pass


def _usable_secret(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\{\{\s*secret\.[^}]+\}\}", text, flags=re.IGNORECASE):
        return ""
    return text


async def _post_with_retry(
    client: httpx.AsyncClient,
    *,
    url: str,
    token: str,
    payload: dict[str, Any],
    attempts: int,
) -> dict[str, Any]:
    last_error = "request not attempted"
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-Trigger-Source": "hive-ai-council",
                },
                json=payload,
            )
            if response.status_code < 400:
                try:
                    body = response.json()
                except ValueError:
                    body = {"ok": True, "http_status": response.status_code}
                return {"ok": True, "attempt": attempt, "http_status": response.status_code, "response": body}
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
            if response.status_code not in _TRANSIENT:
                break
        except httpx.HTTPError as exc:
            last_error = f"{exc.__class__.__name__}: {exc}"
        if attempt < attempts:
            await asyncio.sleep(min(8.0, 1.0 * (2 ** (attempt - 1))))
    raise ModelSyncError(last_error)


async def _pause_rams_if_woken(
    client: httpx.AsyncClient,
    settings: Settings,
    wake: WakeResult | None,
) -> dict[str, Any]:
    if wake is None or wake.already_online:
        return {"attempted": False, "reason": "already-online" if wake else "not-woken"}
    if not settings.koyeb_token.strip() or not settings.koyeb_service_id_rams.strip():
        return {"attempted": False, "reason": "koyeb-control-not-configured"}
    try:
        result = await service_action(
            client,
            token=settings.koyeb_token,
            service_id=settings.koyeb_service_id_rams,
            action="pause",
        )
        return {"attempted": True, "ok": True, **result}
    except KoyebControlError as exc:
        return {"attempted": True, "ok": False, "error": str(exc)}


async def sync_model_registry_downstream(
    settings: Settings,
    *,
    source_run_id: str,
    registry: dict[str, list[dict[str, object]]],
) -> dict[str, Any]:
    """Wake dependencies, persist model defaults in AIMS/RAMS, and verify success."""
    if not settings.model_governance_sync_enabled:
        return {"ok": True, "enabled": False, "reason": "disabled"}

    aims_token = _usable_secret(settings.aims_api_key)
    rams_token = _usable_secret(settings.rams_api_key)
    missing = []
    if not aims_token:
        missing.append("AIMS_API_KEY")
    if not rams_token:
        missing.append("RMS_API_KEY/RAMS_API_KEY")
    if not settings.aims_base_url.strip():
        missing.append("AIMS_BASE_URL")
    if not settings.rams_base_url.strip():
        missing.append("RAMS_BASE_URL")
    if missing:
        raise ModelSyncError(f"Model governance sync is enabled but configuration is missing: {', '.join(missing)}")

    timeout = httpx.Timeout(settings.model_governance_sync_timeout_seconds)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    payload = {"sourceRunId": source_run_id, "registry": registry}
    rams_wake: WakeResult | None = None
    results: dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=timeout, limits=limits, follow_redirects=True) as client:
        # AIMS is configured as always-on, but readiness-gate it so a deployment/restart
        # cannot turn a monthly governance run into a lost update. RAMS remains demand-woken.
        try:
            await ensure_service_ready(settings, "AIMS", client=client)
            rams_wake = await ensure_service_ready(settings, "RAMS", client=client)
        except ServiceLifecycleError as exc:
            raise ModelSyncError(f"downstream service readiness failed: {exc}") from exc
        try:
            results["aims"] = await _post_with_retry(
                client,
                url=f"{settings.aims_base_url.rstrip('/')}/ops/model-governance/apply",
                token=aims_token,
                payload=payload,
                attempts=settings.model_governance_sync_attempts,
            )
            results["rams"] = await _post_with_retry(
                client,
                url=f"{settings.rams_base_url.rstrip('/')}/ops/model-governance/apply",
                token=rams_token,
                payload=payload,
                attempts=settings.model_governance_sync_attempts,
            )
        finally:
            results["rams_cleanup"] = await _pause_rams_if_woken(client, settings, rams_wake)

    return {
        "ok": bool(results.get("aims", {}).get("ok") and results.get("rams", {}).get("ok")),
        "enabled": True,
        "sourceRunId": source_run_id,
        "targets": results,
    }
