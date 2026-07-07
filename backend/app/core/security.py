from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.rate_limit import auth_rate_limiter, client_ip_from_request, token_prefix

bearer = HTTPBearer(auto_error=False)
_LOCAL_DEVELOPMENT_SENTINEL = "change-me-local-only"


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    """Protect admin/API routes while allowing local development convenience."""

    if settings.is_dev and settings.admin_bearer_token == _LOCAL_DEVELOPMENT_SENTINEL:
        return

    client_ip = client_ip_from_request(request)
    supplied = credentials.credentials.strip() if credentials else ""
    prefix = token_prefix(supplied)

    # IP- and token-scoped lockout: checked before validating credentials so a
    # client already locked out cannot use this call to keep probing tokens.
    auth_rate_limiter.check(client_ip, prefix)

    if not credentials or credentials.scheme.lower() != "bearer":
        auth_rate_limiter.record_failure(client_ip, prefix)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    expected = settings.admin_bearer_token.strip()
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        auth_rate_limiter.record_failure(client_ip, prefix)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bearer token")

    auth_rate_limiter.record_success(client_ip, prefix)
