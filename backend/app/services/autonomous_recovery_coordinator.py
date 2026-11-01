"""Durable recovery coordinator using HIVE's existing D1 and private R2 clients.

All operations fail closed. No model calls, GitHub merge, or deployment actions.
"""
from __future__ import annotations

import time
from typing import Any

from app.services.autonomous_recovery_sql import (
    CLAIM_INCIDENT,
    CREATE_COORDINATION_SCHEMA,
    RELEASE_INCIDENT,
    claim_parameters,
)
from app.services.autonomous_recovery_evidence import RecoveryReceipt, evidence_key, receipt_payload
from app.storage.d1 import D1MetadataStore
from app.storage.r2 import R2Storage


def _first_meta(result: dict[str, Any]) -> dict[str, Any]:
    entries = result.get("result")
    if not isinstance(entries, list):
        return {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("meta"), dict):
            return entry["meta"]
    return {}


def _changed(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        raise RuntimeError("D1 operation failed")
    meta = _first_meta(result)
    changed = meta.get("changes")
    if not isinstance(changed, int):
        raise RuntimeError("D1 change count unavailable; cannot prove ownership")
    return changed == 1


class RecoveryCoordinator:
    def __init__(self, d1: D1MetadataStore, r2: R2Storage, bucket: str):
        self.d1 = d1
        self.r2 = r2
        self.bucket = bucket

    def init_schema(self) -> bool:
        if not self.d1.enabled:
            return False
        for statement in CREATE_COORDINATION_SCHEMA.split(";"):
            if statement.strip() and not self.d1.query(statement.strip(), []).get("ok"):
                return False
        return True

    def register(self, *, incident_id: str, repository: str, revision: str, signal: str) -> bool:
        if not all((incident_id, repository, revision, signal)):
            raise ValueError("incident identity required")
        now = int(time.time())
        result = self.d1.query(
            "INSERT OR IGNORE INTO recovery_incidents "
            "(incident_id, repository, revision, signal, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [incident_id, repository, revision, signal, now, now],
        )
        if not result.get("ok"):
            return False
        # INSERT OR IGNORE must never disguise a conflicting incident identity.
        existing = self.d1.query(
            "SELECT repository, revision, signal FROM recovery_incidents "
            "WHERE incident_id = ?",
            [incident_id],
        )
        if not existing.get("ok"):
            return False
        entries = existing.get("result")
        if not isinstance(entries, list):
            return False
        rows = [
            row for entry in entries if isinstance(entry, dict)
            for row in entry.get("results", []) if isinstance(row, dict)
        ]
        if len(rows) != 1:
            return False
        return all(rows[0].get(field) == value for field, value in (
            ("repository", repository), ("revision", revision), ("signal", signal),
        ))

    def claim(
        self, *, incident_id: str, revision: str, owner: str,
        max_attempts: int = 3, lease_seconds: int = 600,
    ) -> bool:
        now = int(time.time())
        result = self.d1.query(
            CLAIM_INCIDENT,
            list(claim_parameters(
                owner=owner, now=now, lease_seconds=lease_seconds,
                incident_id=incident_id, revision=revision, max_attempts=max_attempts,
            )),
        )
        if not _changed(result):
            return False
        # The claim is already fenced in D1. Record the attempt only while
        # this owner still holds the exact revision's live lease.
        audit = self.d1.query(
            "INSERT INTO recovery_attempts "
            "(incident_id, attempt, owner, revision, started_at) "
            "SELECT incident_id, attempts, owner, revision, ? "
            "FROM recovery_incidents "
            "WHERE incident_id = ? AND revision = ? AND owner = ? "
            "AND lease_until > ? AND status = 'open'",
            [now, incident_id, revision, owner, now],
        )
        if not _changed(audit):
            raise RuntimeError("Recovery lease acquired but attempt audit not confirmed")
        return True

    def release(
        self, *, incident_id: str, revision: str, owner: str,
        outcome: str, cooldown_seconds: int = 300, error_code: str | None = None,
    ) -> bool:
        if outcome not in {"open", "held", "escalated", "verified"}:
            raise ValueError("invalid outcome")
        if not 0 <= cooldown_seconds <= 86400:
            raise ValueError("invalid cooldown")
        now = int(time.time())
        # A verified release must have durable, owner-fenced evidence for the
        # current attempt. An unverified worker cannot declare itself healthy.
        if outcome == "verified":
            proof = self.d1.query(
                "SELECT a.evidence_key FROM recovery_attempts a "
                "JOIN recovery_incidents i ON i.incident_id = a.incident_id "
                "WHERE a.incident_id = ? AND a.revision = ? AND a.owner = ? "
                "AND a.attempt = i.attempts AND a.finished_at IS NULL "
                "AND a.evidence_key IS NOT NULL AND i.revision = ? "
                "AND i.owner = ? AND i.status = 'open' AND i.lease_until > ?",
                [incident_id, revision, owner, revision, owner, now],
            )
            if not proof.get("ok"):
                raise RuntimeError("Recovery verification evidence unavailable")
            entries = proof.get("result")
            rows = [
                row for entry in entries if isinstance(entry, dict)
                for row in entry.get("results", []) if isinstance(row, dict)
            ] if isinstance(entries, list) else []
            if len(rows) != 1 or not rows[0].get("evidence_key"):
                raise RuntimeError("Verified recovery requires a persisted receipt")
        result = self.d1.query(
            RELEASE_INCIDENT,
            [outcome, now + cooldown_seconds, error_code, now,
             incident_id, revision, owner, now],
        )
        if not _changed(result):
            return False
        audit = self.d1.query(
            "UPDATE recovery_attempts SET finished_at = ?, outcome = ? "
            "WHERE incident_id = ? AND revision = ? AND owner = ? "
            "AND finished_at IS NULL AND attempt = "
            "(SELECT attempts FROM recovery_incidents WHERE incident_id = ?)",
            [now, "failed" if outcome == "open" else outcome,
             incident_id, revision, owner, incident_id],
        )
        if not _changed(audit):
            raise RuntimeError("Recovery outcome released but attempt audit not confirmed")
        return True

    def record_receipt(
        self, *, receipt: RecoveryReceipt, owner: str,
    ) -> str:
        """Persist immutable evidence and fence its audit link to the live lease.

        A receipt alone cannot certify a deployment or release an incident.
        """
        if not owner:
            raise ValueError("recovery owner required")
        now = int(time.time())
        ownership = self.d1.query(
            "SELECT a.attempt FROM recovery_attempts a "
            "JOIN recovery_incidents i ON i.incident_id = a.incident_id "
            "WHERE a.incident_id = ? AND a.revision = ? AND a.attempt = ? "
            "AND a.owner = ? AND a.finished_at IS NULL AND a.evidence_key IS NULL "
            "AND i.owner = ? AND i.revision = ? AND i.attempts = a.attempt "
            "AND i.status = 'open' AND i.lease_until > ?",
            [receipt.incident_id, receipt.revision, receipt.attempt,
             owner, owner, receipt.revision, now],
        )
        if not ownership.get("ok"):
            raise RuntimeError("Recovery evidence ownership check failed")
        entries = ownership.get("result")
        rows = [
            row for entry in entries if isinstance(entry, dict)
            for row in entry.get("results", []) if isinstance(row, dict)
        ] if isinstance(entries, list) else []
        if len(rows) != 1:
            raise RuntimeError("Recovery evidence lease not held")
        key = self.write_receipt(receipt)
        result = self.d1.query(
            "UPDATE recovery_attempts SET evidence_key = ? "
            "WHERE incident_id = ? AND revision = ? AND attempt = ? "
            "AND owner = ? AND finished_at IS NULL AND evidence_key IS NULL "
            "AND EXISTS (SELECT 1 FROM recovery_incidents i "
            "WHERE i.incident_id = recovery_attempts.incident_id "
            "AND i.revision = ? AND i.owner = ? "
            "AND i.attempts = recovery_attempts.attempt "
            "AND i.status = 'open' AND i.lease_until > ?)",
            [key, receipt.incident_id, receipt.revision, receipt.attempt,
             owner, receipt.revision, owner, int(time.time())],
        )
        if not _changed(result):
            raise RuntimeError("Recovery evidence audit link not confirmed")
        return key

    def write_receipt(self, receipt: RecoveryReceipt) -> str:
        if not self.bucket or not self.r2.write_enabled:
            raise RuntimeError("Private R2 evidence storage not configured")
        payload = receipt_payload(receipt)
        key = evidence_key(receipt.incident_id, receipt.revision, receipt.attempt, payload)
        self.r2._assert_bucket_allowed(self.bucket)
        self.r2.client().put_object(
            Bucket=self.bucket, Key=key, Body=payload,
            ContentType="application/json", IfNoneMatch="*",
        )
        return key
