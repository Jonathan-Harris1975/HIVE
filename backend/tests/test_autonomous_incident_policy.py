"""Deterministic tests for autonomous incident policy; no credentials or network."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.autonomous_incident_policy import (
    RecoveryPolicy,
    RecoverySnapshot,
    incident_fingerprint,
    next_action,
)


NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def snapshot(**overrides):
    fields = dict(
        attempts=0,
        last_attempt_at=None,
        lease_expires_at=None,
        owner=None,
        exact_head_green=True,
        trusted_admission=True,
    )
    fields.update(overrides)
    return RecoverySnapshot(**fields)


def test_identity_is_stable_and_revision_scoped():
    a = incident_fingerprint("owner/repo", "ci", "test", "abc")
    assert a == incident_fingerprint("owner/repo", "ci", "test", "abc")
    assert a != incident_fingerprint("owner/repo", "ci", "test", "def")
    with pytest.raises(ValueError):
        incident_fingerprint("owner/repo", "", "test", "abc")


@pytest.mark.parametrize(
    "change",
    [{"exact_head_green": False}, {"trusted_admission": False}],
)
def test_admission_is_fail_closed(change):
    assert next_action(snapshot(**change), RecoveryPolicy(), now=NOW, requesting_owner="worker") == "hold"


def test_foreign_lease_blocks_and_expired_lease_releases():
    policy = RecoveryPolicy()
    live = snapshot(owner="other", lease_expires_at=NOW + timedelta(seconds=30))
    assert next_action(live, policy, now=NOW, requesting_owner="worker") == "hold"
    assert next_action(live, policy, now=NOW + timedelta(seconds=31), requesting_owner="worker") == "retry"


def test_retry_cooldown_and_escalation():
    policy = RecoveryPolicy(max_attempts=3, cooldown_seconds=300)
    assert next_action(snapshot(attempts=1, last_attempt_at=NOW), policy, now=NOW, requesting_owner="worker") == "hold"
    assert next_action(snapshot(attempts=1, last_attempt_at=NOW), policy, now=NOW + timedelta(seconds=300), requesting_owner="worker") == "retry"
    assert next_action(snapshot(attempts=3), policy, now=NOW, requesting_owner="worker") == "escalate"


def test_verified_requires_explicit_evidence_flag():
    assert next_action(snapshot(deployment_verified=True), RecoveryPolicy(), now=NOW, requesting_owner="worker") == "verified"
    assert next_action(snapshot(), RecoveryPolicy(), now=NOW, requesting_owner="worker") == "retry"


def test_reject_naive_timestamps_and_invalid_policy():
    with pytest.raises(ValueError):
        snapshot(last_attempt_at=datetime(2026, 10, 31))
    with pytest.raises(ValueError):
        RecoveryPolicy(max_attempts=0)
    with pytest.raises(ValueError):
        next_action(snapshot(), RecoveryPolicy(), now=datetime(2026, 10, 31), requesting_owner="worker")
