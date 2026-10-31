"""Deterministic, fail-closed incident identity and bounded recovery policy.

This module performs no network calls or deployment actions. Persist claims and
receipts in a transactional coordinator before enabling any multi-worker use.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from typing import Literal

Outcome = Literal["retry", "escalate", "hold", "verified"]


def incident_fingerprint(repository: str, source: str, signal: str, revision: str) -> str:
    """Stable identifier for the same failing signal at an exact revision."""
    fields = (repository, source, signal, revision)
    if any(not isinstance(value, str) or not value.strip() for value in fields):
        raise ValueError("incident identity fields must be non-empty strings")
    payload = json.dumps(fields, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True)
class RecoveryPolicy:
    max_attempts: int = 3
    cooldown_seconds: int = 300
    lease_seconds: int = 600

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if self.cooldown_seconds < 0 or not 1 <= self.lease_seconds <= 3600:
            raise ValueError("invalid cooldown or lease")


@dataclass(frozen=True)
class RecoverySnapshot:
    attempts: int
    last_attempt_at: datetime | None
    lease_expires_at: datetime | None
    owner: str | None
    exact_head_green: bool
    trusted_admission: bool
    deployment_verified: bool = False

    def __post_init__(self) -> None:
        if self.attempts < 0:
            raise ValueError("attempts cannot be negative")
        for timestamp in (self.last_attempt_at, self.lease_expires_at):
            if timestamp is not None and (timestamp.tzinfo is None or timestamp.utcoffset() is None):
                raise ValueError("timestamps must be timezone-aware")


def next_action(
    snapshot: RecoverySnapshot,
    policy: RecoveryPolicy,
    *,
    now: datetime,
    requesting_owner: str,
) -> Outcome:
    """Pure decision function; never grants a lease or authorises a merge."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not requesting_owner:
        raise ValueError("requesting_owner required")
    if snapshot.deployment_verified:
        return "verified"
    if not snapshot.exact_head_green or not snapshot.trusted_admission:
        return "hold"
    if (
        snapshot.lease_expires_at is not None
        and snapshot.lease_expires_at > now
        and snapshot.owner != requesting_owner
    ):
        return "hold"
    if snapshot.attempts >= policy.max_attempts:
        return "escalate"
    if (
        snapshot.last_attempt_at is not None
        and snapshot.last_attempt_at + timedelta(seconds=policy.cooldown_seconds) > now
    ):
        return "hold"
    return "retry"
