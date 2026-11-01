"""Read-only, authenticated autonomous recovery operations API.

Never starts repairs or changes admission. Missing durable storage fails closed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.core.config import Settings, get_settings
from app.services.autonomous_recovery_diagnosis import diagnose_signal
from app.core.security import require_admin
from app.storage.d1 import D1MetadataStore

router = APIRouter(tags=["recovery"], dependencies=[Depends(require_admin)])

def _rows(result: object) -> list[dict[str, object]]:
    if not isinstance(result, list):
        return []
    rows: list[dict[str, object]] = []
    for entry in result:
        if isinstance(entry, dict) and isinstance(entry.get("results"), list):
            rows.extend(item for item in entry["results"] if isinstance(item, dict))
    return rows


@router.get("/system/recovery/status")
def recovery_status(
    response: Response,
    limit: int = Query(50, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Operator-readable snapshot; no credentials, raw traces or untrusted logs."""
    response.headers["Cache-Control"] = "no-store"
    store = D1MetadataStore(settings)
    if not store.enabled:
        return {
            "ok": False, "configured": False, "schema_ready": False,
            "mode": "disabled", "incidents": [],
            "message": "Durable D1 recovery coordination is not configured",
        }
    probe = store.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'recovery_incidents'",
        [],
    )
    if not probe.get("ok"):
        raise HTTPException(status_code=503, detail="Recovery storage unavailable")
    if not _rows(probe.get("result")):
        return {
            "ok": False, "configured": True, "schema_ready": False,
            "mode": "inactive", "incidents": [],
            "message": "Recovery schema not installed",
        }
    attempts_probe = store.query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'recovery_attempts'",
        [],
    )
    if not attempts_probe.get("ok"):
        raise HTTPException(status_code=503, detail="Recovery storage unavailable")
    if not _rows(attempts_probe.get("result")):
        return {
            "ok": False, "configured": True, "schema_ready": False,
            "mode": "inactive", "incidents": [],
            "message": "Recovery attempts schema not installed",
        }
    result = store.query(
        "SELECT incident_id, repository, revision, signal, status, attempts, "
        "lease_until, next_attempt_after, last_error_code, created_at, updated_at "
        "FROM recovery_incidents ORDER BY updated_at DESC LIMIT ?",
        [limit],
    )
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail="Recovery status unavailable")
    incidents = _rows(result.get("result"))
    for incident in incidents:
        diagnosis = diagnose_signal(str(incident.get("signal") or ""))
        incident["diagnosis"] = {
            "category": diagnosis.category,
            "confidence": diagnosis.confidence,
            "next_step": diagnosis.next_step,
            "automatic_patch_allowed": diagnosis.automatic_patch_allowed,
        }
    return {
        "ok": True, "configured": True, "schema_ready": True,
        "mode": "observation_only", "count": len(incidents), "incidents": incidents,
    }
