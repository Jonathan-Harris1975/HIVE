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
        return bool(result.get("ok"))

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
        return _changed(result)

    def release(
        self, *, incident_id: str, revision: str, owner: str,
        outcome: str, cooldown_seconds: int = 300, error_code: str | None = None,
    ) -> bool:
        if outcome not in {"open", "held", "escalated", "verified"}:
            raise ValueError("invalid outcome")
        if not 0 <= cooldown_seconds <= 86400:
            raise ValueError("invalid cooldown")
        now = int(time.time())
        result = self.d1.query(
            RELEASE_INCIDENT,
            [outcome, now + cooldown_seconds, error_code, now,
             incident_id, revision, owner, now],
        )
        return _changed(result)

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
