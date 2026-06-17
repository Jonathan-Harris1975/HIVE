from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from app.core.config import Settings, get_settings
from app.core.security import bearer
from app.services.ops_events import ingest_ops_event

router = APIRouter(tags=["ops-events"])


def require_ops_event_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.ops_event_ingest_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational event ingestion is disabled")
    expected = settings.ops_event_ingest_token.strip()
    supplied = credentials.credentials.strip() if credentials and credentials.scheme.lower() == "bearer" else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid operational event token",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/ops/events", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_ops_event_token)])
async def receive_ops_event(
    payload: dict[str, Any] = Body(...),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Accept a bounded, redacted operational event from trusted providers."""

    return await asyncio.to_thread(ingest_ops_event, settings, payload)
