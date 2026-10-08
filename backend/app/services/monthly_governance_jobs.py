"""Atomic D1 claim for a monthly governance job.

This is a persistence primitive, not a background worker. The caller must run
work outside the HTTP request and explicitly mark its result. A claimed job
must never be silently retried after an ambiguous worker failure.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.storage.d1 import D1MetadataStore, _extract_d1_rows

LANE = "monthly_governance_jobs"


def _job_id(period: str) -> str:
    return f"monthly-governance:{period}"


def claim_job(store: D1MetadataStore, *, period: str, owner: str) -> dict[str, Any]:
    """Atomically claim an unclaimed month; never overwrite an existing job."""
    if not store.enabled:
        return {"ok": False, "claimed": False, "error": "D1 unavailable"}
    if not owner.strip():
        raise ValueError("job owner is required")
    from app.services.monthly_review import _period_bounds
    _period_bounds(period)
    now = datetime.now(UTC).isoformat()
    job_id = _job_id(period)
    result = store.query(
        """
        INSERT INTO hive_ecosystem_metadata
        (id, lane, source_type, source_id, title, url, metadata_json, created_at, updated_at)
        VALUES (?, ?, 'job', ?, 'Monthly governance job', NULL,
                json_object('status','claimed','owner',?,'claimed_at',?), ?, ?)
        ON CONFLICT(id) DO NOTHING
        RETURNING id
        """,
        [job_id, LANE, period, owner, now, now, now],
    )
    if not result.get("ok"):
        return {"ok": False, "claimed": False, "error": "D1 claim failed"}
    rows = _extract_d1_rows(result.get("result"))
    return {"ok": True, "claimed": bool(rows), "job_id": job_id}


def get_job(store: D1MetadataStore, *, period: str) -> dict[str, Any]:
    if not store.enabled:
        return {"ok": False, "found": False, "error": "D1 unavailable"}
    from app.services.monthly_review import _period_bounds
    _period_bounds(period)
    result = store.query(
        "SELECT id, source_id, metadata_json, created_at, updated_at "
        "FROM hive_ecosystem_metadata WHERE id = ? AND lane = ?",
        [_job_id(period), LANE],
    )
    if not result.get("ok"):
        return {"ok": False, "error": "D1 lookup failed"}
    rows = _extract_d1_rows(result.get("result"))
    if not rows:
        return {"ok": True, "found": False}
    row = rows[0]
    return {"ok": True, "found": True, "job_id": row["id"],
            "period": row["source_id"], "state": json.loads(row.get("metadata_json") or "{}"),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def complete_job(
    store: D1MetadataStore, *, period: str, owner: str, succeeded: bool
) -> dict[str, Any]:
    """Finish only a job claimed by this owner, exactly once.

    Ambiguous worker failures remain claimed for manual investigation. This
    operation does not automatically reclaim or repeat downstream writes.
    """
    if not store.enabled:
        return {"ok": False, "completed": False, "error": "D1 unavailable"}
    from app.services.monthly_review import _period_bounds
    _period_bounds(period)
    if not owner.strip():
        raise ValueError("job owner is required")
    now = datetime.now(UTC).isoformat()
    status = "completed" if succeeded else "failed"
    result = store.query(
        """
        UPDATE hive_ecosystem_metadata
        SET metadata_json = json_set(metadata_json, '$.status', ?, '$.finished_at', ?),
            updated_at = ?
        WHERE id = ? AND lane = ?
          AND json_extract(metadata_json, '$.status') = 'claimed'
          AND json_extract(metadata_json, '$.owner') = ?
        RETURNING id
        """,
        [status, now, now, _job_id(period), LANE, owner],
    )
    if not result.get("ok"):
        return {"ok": False, "completed": False, "error": "D1 completion failed"}
    return {"ok": True, "completed": bool(_extract_d1_rows(result.get("result"))),
            "status": status}
