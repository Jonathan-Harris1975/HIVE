from __future__ import annotations

import hashlib
import ipaddress
import os
import time
from dataclasses import dataclass, field
from threading import Lock

from fastapi import HTTPException, Request, status

# Auth-failure rate limiting is intentionally process-local because HIVE is
# deployed as a single worker. Two scopes are enforced:
#   1. a tighter (IP, token fingerprint) limit for repeated credential guesses;
#   2. a broader IP-only limit so rotating the guessed token cannot bypass the
#      first limit indefinitely.
# If HIVE is horizontally scaled, move these counters to a shared store.
DEFAULT_MAX_FAILURES = 10
DEFAULT_IP_MAX_FAILURES = 30
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_LOCKOUT_SECONDS = 300.0


@dataclass
class _FailureRecord:
    failures: list[float] = field(default_factory=list)
    locked_until: float | None = None


class AuthRateLimiter:
    """Sliding-window auth limiter with credential- and IP-scoped lockouts."""

    def __init__(
        self,
        *,
        max_failures: int = DEFAULT_MAX_FAILURES,
        ip_max_failures: int = DEFAULT_IP_MAX_FAILURES,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        lockout_seconds: float = DEFAULT_LOCKOUT_SECONDS,
        clock=time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.ip_max_failures = ip_max_failures
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self._clock = clock
        self._lock = Lock()
        self._records: dict[str, _FailureRecord] = {}
        self._ip_records: dict[str, _FailureRecord] = {}

    @staticmethod
    def _key(client_ip: str, token_fingerprint: str) -> str:
        return f"{client_ip}:{token_fingerprint}"

    def _check_record(
        self,
        records: dict[str, _FailureRecord],
        key: str,
        now: float,
    ) -> None:
        record = records.get(key)
        if not record or record.locked_until is None:
            return
        if now < record.locked_until:
            retry_after = int(record.locked_until - now) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many authentication failures. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        records.pop(key, None)

    def check(self, client_ip: str, token_fingerprint: str) -> None:
        """Raise HTTP 429 if the source IP or credential guess is locked out."""

        now = self._clock()
        key = self._key(client_ip, token_fingerprint)
        with self._lock:
            self._check_record(self._ip_records, client_ip, now)
            self._check_record(self._records, key, now)

    def _record_failure(
        self,
        records: dict[str, _FailureRecord],
        key: str,
        *,
        threshold: int,
        now: float,
    ) -> None:
        record = records.setdefault(key, _FailureRecord())
        record.failures = [t for t in record.failures if now - t < self.window_seconds]
        record.failures.append(now)
        if len(record.failures) >= threshold:
            record.locked_until = now + self.lockout_seconds

    def record_failure(self, client_ip: str, token_fingerprint: str) -> None:
        now = self._clock()
        key = self._key(client_ip, token_fingerprint)
        with self._lock:
            self._record_failure(
                self._records,
                key,
                threshold=self.max_failures,
                now=now,
            )
            self._record_failure(
                self._ip_records,
                client_ip,
                threshold=self.ip_max_failures,
                now=now,
            )

    def record_success(self, client_ip: str, token_fingerprint: str) -> None:
        """Clear recent failures after a valid authentication from this source."""

        key = self._key(client_ip, token_fingerprint)
        with self._lock:
            self._records.pop(key, None)
            self._ip_records.pop(client_ip, None)

    def reset(self) -> None:
        """Test-only helper to clear all state between test cases."""

        with self._lock:
            self._records.clear()
            self._ip_records.clear()


# Process-wide singleton used by app.core.security.require_admin.
auth_rate_limiter = AuthRateLimiter()


def client_ip_from_request(request: Request) -> str:
    """Return a non-spoofable client address for authentication throttling.

    Koyeb documents that it appends the connecting client address to the end
    of ``X-Forwarded-For`` and certifies that final value. We use that value
    only when Koyeb's runtime marker is present. Everywhere else, the network
    peer selected by the ASGI server is used and arbitrary forwarded headers
    are ignored.
    """

    if os.getenv("KOYEB_PUBLIC_DOMAIN"):
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            candidate = forwarded_for.rsplit(",", 1)[-1].strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass

    if request.client:
        return request.client.host
    return "unknown"


def token_fingerprint(token: str | None) -> str:
    """Return a non-reversible tag suitable for rate-limit bucket keys."""

    if not token:
        return "none"
    return hashlib.sha256(token.encode("utf-8", errors="ignore")).hexdigest()[:16]


def token_prefix(token: str | None) -> str:
    """Backward-compatible alias for the former raw-prefix helper."""

    return token_fingerprint(token)
