"""Portable SQL schema for durable recovery coordination.

Apply to a dedicated Cloudflare D1 database before enabling workers.
All timestamps are UTC Unix seconds. Only the lease owner may advance an attempt.
"""

CREATE_COORDINATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS recovery_incidents (
  incident_id TEXT PRIMARY KEY NOT NULL,
  repository TEXT NOT NULL,
  revision TEXT NOT NULL,
  signal TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'held', 'escalated', 'verified')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  owner TEXT,
  lease_until INTEGER,
  next_attempt_after INTEGER NOT NULL DEFAULT 0,
  last_error_code TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recovery_incidents_ready
  ON recovery_incidents(status, next_attempt_after, lease_until);
CREATE TABLE IF NOT EXISTS recovery_attempts (
  incident_id TEXT NOT NULL REFERENCES recovery_incidents(incident_id),
  attempt INTEGER NOT NULL CHECK (attempt > 0),
  owner TEXT NOT NULL,
  revision TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  finished_at INTEGER,
  outcome TEXT CHECK (outcome IN ('failed', 'held', 'escalated', 'verified')),
  evidence_key TEXT,
  PRIMARY KEY (incident_id, attempt)
);
"""

# Execute this as a *single* SQL statement with bound parameters and inspect
# rows affected. The atomic WHERE condition prevents duplicate worker claims.
CLAIM_INCIDENT = """
UPDATE recovery_incidents
SET owner = ?,
    lease_until = ?,
    attempts = attempts + 1,
    updated_at = ?
WHERE incident_id = ?
  AND revision = ?
  AND status = 'open'
  AND attempts < ?
  AND next_attempt_after <= ?
  AND (lease_until IS NULL OR lease_until <= ?)
"""

# A stale worker must never overwrite a newer lease holder's outcome.
RELEASE_INCIDENT = """
UPDATE recovery_incidents
SET status = ?,
    owner = NULL,
    lease_until = NULL,
    next_attempt_after = ?,
    last_error_code = ?,
    updated_at = ?
WHERE incident_id = ?
  AND revision = ?
  AND owner = ?
  AND lease_until > ?
  AND status = 'open'
"""


def claim_parameters(
    *,
    owner: str,
    now: int,
    lease_seconds: int,
    incident_id: str,
    revision: str,
    max_attempts: int,
) -> tuple[object, ...]:
    if not owner or not incident_id or not revision:
        raise ValueError("owner, incident_id and revision are required")
    if now < 0 or not 1 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 10:
        raise ValueError("invalid lease or retry parameters")
    return (owner, now + lease_seconds, now, incident_id, revision, max_attempts, now, now)
