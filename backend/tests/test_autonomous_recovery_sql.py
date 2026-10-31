"""SQLite contract tests for the D1-compatible atomic claim statement."""
import sqlite3

import pytest

from app.services.autonomous_recovery_sql import (
    CLAIM_INCIDENT,
    CREATE_COORDINATION_SCHEMA,
    RELEASE_INCIDENT,
    claim_parameters,
)


def database():
    db = sqlite3.connect(":memory:")
    db.executescript(CREATE_COORDINATION_SCHEMA)
    db.execute(
        """INSERT INTO recovery_incidents
        (incident_id, repository, revision, signal, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        ("incident-1", "owner/repo", "sha-1", "ci", 100, 100),
    )
    return db


def test_atomic_claim_is_exclusive_and_bounded():
    db = database()
    first = db.execute(
        CLAIM_INCIDENT,
        claim_parameters(
            owner="worker-a", now=200, lease_seconds=60,
            incident_id="incident-1", revision="sha-1", max_attempts=2,
        ),
    )
    assert first.rowcount == 1
    second = db.execute(
        CLAIM_INCIDENT,
        claim_parameters(
            owner="worker-b", now=201, lease_seconds=60,
            incident_id="incident-1", revision="sha-1", max_attempts=2,
        ),
    )
    assert second.rowcount == 0
    assert db.execute("SELECT attempts FROM recovery_incidents").fetchone() == (1,)


def test_stale_owner_cannot_release_new_lease():
    db = database()
    db.execute(CLAIM_INCIDENT, claim_parameters(
        owner="worker-a", now=200, lease_seconds=60,
        incident_id="incident-1", revision="sha-1", max_attempts=3,
    ))
    db.execute(CLAIM_INCIDENT, claim_parameters(
        owner="worker-b", now=261, lease_seconds=60,
        incident_id="incident-1", revision="sha-1", max_attempts=3,
    ))
    result = db.execute(
        RELEASE_INCIDENT,
        ("verified", 0, None, 262, "incident-1", "sha-1", "worker-a", 262),
    )
    assert result.rowcount == 0
    assert db.execute("SELECT owner FROM recovery_incidents").fetchone() == ("worker-b",)


def test_revision_is_fenced():
    db = database()
    result = db.execute(CLAIM_INCIDENT, claim_parameters(
        owner="worker", now=200, lease_seconds=60,
        incident_id="incident-1", revision="sha-2", max_attempts=3,
    ))
    assert result.rowcount == 0


def test_invalid_claim_arguments_rejected():
    with pytest.raises(ValueError):
        claim_parameters(
            owner="", now=1, lease_seconds=60,
            incident_id="id", revision="sha", max_attempts=3,
        )
