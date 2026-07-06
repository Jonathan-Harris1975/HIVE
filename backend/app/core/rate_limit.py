from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Lock

from fastapi import HTTPException, Request, status

# ---------------------------------------------------------------------------
# Auth-failure rate limiting.
#
# HIVE previously had NO rate limiting on its own API surface — the only
# throttle in the whole system was the HIVE-UI Cloudflare Function login
# endpoint, which does not protect the backend's own bearer-token-guarded
# routes at all (e.g. someone hitting the Koyeb URL directly).
#
# This module adds an in-process, IP- and token-scoped sliding-window lockout
# on consecutive authentication failures. It is intentionally simple:
#   - in-memory only (per-process). On a single-instance Koyeb deployment
#     this is sufficient; if HIVE is ever horizontally scaled, this should be
#     backed by a shared store (Redis / D1) instead, since each replica would
#     otherwise track its own counters independently.
#   - scoped by (client IP, first 8 chars of the supplied token) so that one
#     bad token from one IP does not lock out a different, legitimate client
#     sharing the same IP (e.g. behind a corporate NAT) using a different
#     token, while still catching repeated guesses of the same bad token.
# ---------------------------------------------------------------------------

DEFAULT_MAX_FAILURES = 10
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_LOCKOUT_SECONDS = 300.0


@dataclass
class _FailureRecord:
    failures: list[float] = field(default_factory=list)
    locked_until: float | None = None


class AuthRateLimiter:
    """Sliding-window auth-failure limiter, keyed by (ip, token-prefix)."""

    def __init__(
        self,
        *,
        max_failures: int = DEFAULT_MAX_FAILURES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        lockout_seconds: float = DEFAULT_LOCKOUT_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._lock = Lock()
        self._records: dict[str, _FailureRecord] = {}

    @staticmethod
    def _key(client_ip: str, token_prefix: str) -> str:
        return f"{client_ip}:{token_prefix}"

    def check(self, client_ip: str, token_prefix: str) -> None:
        """Raise HTTP 429 if this (ip, token-prefix) pair is currently locked out."""
        now = self._clock()
        key = self._key(client_ip, token_prefix)
        with self._lock:
            record = self._records.get(key)
            if record and record.locked_until is not None:
                if now < record.locked_until:
                    retry_after = int(record.locked_until - now) + 1
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Too many authentication failures. Try again later.",
                        headers={"Retry-After": str(retry_after)},
                    )
                # Lockout has expired — reset.
                self._records.pop(key, None)

    def record_failure(self, client_ip: str, token_prefix: str) -> None:
        now = self._clock()
        key = self._key(client_ip, token_prefix)
        with self._lock:
            record = self._records.setdefault(key, _FailureRecord())
            record.failures = [t for t in record.failures if now - t < self.window_seconds]
            record.failures.append(now)
            if len(record.failures) >= self.max_failures:
                record.locked_until = now + self.lockout_seconds

    def record_success(self, client_ip: str, token_prefix: str) -> None:
        key = self._key(client_ip, token_prefix)
        with self._lock:
            self._records.pop(key, None)

    def reset(self) -> None:
        """Test-only helper to clear all state between test cases."""
        with self._lock:
            self._records.clear()


# Process-wide singleton used by app.core.security.require_admin.
auth_rate_limiter = AuthRateLimiter()


def client_ip_from_request(request: Request) -> str:
    """Best-effort client IP extraction.

    Trusts X-Forwarded-For only because HIVE is deployed behind Koyeb's
    proxy (see app.core.production trusted-host enforcement); this mirrors
    the trust boundary already assumed elsewhere in the app for CORS/host
    checks. Falls back to the raw peer address for local/dev runs.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def token_prefix(token: str | None) -> str:
    if not token:
        return "none"
    return token[:8]
