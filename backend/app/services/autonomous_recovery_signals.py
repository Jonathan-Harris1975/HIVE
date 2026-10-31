"""Correlate trusted operational failure signals into durable recovery incidents.

This deliberately records incidents without authorising repairs or releases.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.autonomous_incident_policy import incident_fingerprint
from app.services.autonomous_recovery_coordinator import RecoveryCoordinator

_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_FAILURE_STATES = frozenset({"failure", "failed", "error", "down", "blocked", "unavailable"})
_ALLOWED_SOURCES = frozenset({"github", "koyeb", "cloudflare", "hive"})
_ALLOWED_ENVIRONMENTS = frozenset({"production"})


def correlate_failure(
    event: dict[str, Any],
    coordinator: RecoveryCoordinator,
    *,
    allowed_repositories: frozenset[str],
) -> dict[str, object]:
    """Register an exact-revision production failure, never untrusted repair intent."""
    source = str(event.get("source") or "").strip().lower()
    repository = str(event.get("service") or "").strip()
    revision = str(event.get("release_id") or "").strip().lower()
    state = str(event.get("status") or "").strip().lower()
    environment = str(event.get("environment") or "").strip().lower()
    signal = str(event.get("event_type") or "").strip().lower()
    if (
        source not in _ALLOWED_SOURCES
        or environment not in _ALLOWED_ENVIRONMENTS
        or repository not in allowed_repositories
        or not _SHA.fullmatch(revision)
        or state not in _FAILURE_STATES
        or not signal or len(signal) > 100
    ):
        return {"accepted": False, "reason": "signal_not_eligible"}
    incident_id = incident_fingerprint(repository, source, signal, revision)
    registered = coordinator.register(
        incident_id=incident_id,
        repository=repository,
        revision=revision,
        signal=f"{source}:{signal}",
    )
    return {"accepted": registered, "incident_id": incident_id if registered else None,
            "reason": "recorded" if registered else "storage_unavailable"}
