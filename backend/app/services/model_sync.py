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


def _aims_explicit_assignments(
    registry: dict[str, list[dict[str, object]]],
) -> dict[str, str]:
    """Build service-specific AIMS routes only from evidenced approvals."""
    candidates: dict[str, list[dict[str, object]]] = {}
    for items in registry.values():
        for item in items:
            try:
                sample_size = int(item.get("evaluation_sample_size") or 0)
            except (TypeError, ValueError):
                sample_size = 0
            if (
                item.get("available") is False
                or (
                    str(item.get("provider") or "").lower() == "openrouter"
                    and item.get("available") is not True
                )
                or item.get("evaluation_passed") is not True
                or sample_size < 1
                or not str(item.get("evaluation_evidence") or "").strip()
            ):
                continue
            roles = item.get("approved_roles")
            if not isinstance(roles, (list, tuple)):
                continue
            for role in roles:
                name = str(role).strip()
                if name.startswith(("AI_MODEL_", "OPENROUTER_", "BLOTATO_", "COMMS_HUB_", "OUTREACH_", "NEWSLETTER_")):
                    candidates.setdefault(name, []).append(item)

    def order(item: dict[str, object]) -> tuple[bool, float, float, float, str]:
        raw_cost = item.get("cost_per_successful_task")
        try:
            cost = float(raw_cost) if raw_cost is not None else 0.0
            has_cost = raw_cost is not None and cost >= 0
        except (TypeError, ValueError):
            has_cost, cost = False, 0.0
        try:
            quality = float(item.get("task_score") or item.get("score") or 0.0)
        except (TypeError, ValueError):
            quality = 0.0
        try:
            reliability = float(item.get("reliability_score") or 0.0)
        except (TypeError, ValueError):
            reliability = 0.0
        return (not has_cost, cost, -quality, -reliability, str(item.get("model_id") or ""))

    assignments: dict[str, str] = {}
    for role, items in candidates.items():
        selected = sorted(items, key=order)[0]
        model_id = str(selected.get("model_id") or "")
        if model_id:
            assignments[role] = model_id
    return assignments


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
            if 200 <= response.status_code < 300:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise ModelSyncError("Downstream model governance response was not valid JSON") from exc
                if not isinstance(body, dict) or body.get("ok") is not True:
                    raise ModelSyncError(f"Downstream model governance did not confirm application: {str(body)[:500]}")
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
    payload = {
        "sourceRunId": source_run_id,
        "registry": registry,
        "requireTaskEvaluation": True,
        "assignments": _aims_explicit_assignments(registry),
    }
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
