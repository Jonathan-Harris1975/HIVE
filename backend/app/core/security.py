from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

bearer = HTTPBearer(auto_error=False)
_LOCAL_DEVELOPMENT_SENTINEL = "change-me-local-only"


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    """Protect admin/API routes while allowing local development convenience."""

    if request.url.path == "/health":
        return

    if settings.is_dev and settings.admin_bearer_token == _LOCAL_DEVELOPMENT_SENTINEL:
        return

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    supplied = credentials.credentials.strip()
    expected = settings.admin_bearer_token.strip()
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bearer token")
