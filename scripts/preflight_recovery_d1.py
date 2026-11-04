"""Read-only production preflight for every repository recovery D1."""
from __future__ import annotations

import json

from app.core.config import get_settings
from app.core.governed_repositories import DEFAULT_RECOVERY_D1_DATABASES, GOVERNED_REPOSITORY_IDS
from app.storage.d1 import RecoveryD1Store


def main() -> int:
    settings = get_settings()
    if not settings.recovery_signal_ingest_enabled:
        raise SystemExit("Recovery signal ingestion is disabled in the live HIVE configuration")
    verified = []
    for repository in GOVERNED_REPOSITORY_IDS:
        store = RecoveryD1Store(settings, repository=repository)
        if not store.enabled:
            raise SystemExit(f"Recovery D1 is not configured for {repository}")
        result = store.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('recovery_incidents', 'recovery_attempts') ORDER BY name",
            [],
        )
        rows = [
            row
            for entry in result.get("result", [])
            if isinstance(entry, dict)
            for row in entry.get("results", [])
            if isinstance(row, dict)
        ] if result.get("ok") else []
        names = {str(row.get("name") or "") for row in rows}
        expected = {"recovery_incidents", "recovery_attempts"}
        if names != expected:
            raise SystemExit(f"Recovery D1 schema is incomplete for {repository}")
        verified.append({
            "repository": repository,
            "database_name": DEFAULT_RECOVERY_D1_DATABASES[repository]["name"],
            "schema_ready": True,
        })
    print(json.dumps({
        "event": "recovery.d1.preflight",
        "status": "verified",
        "repository_count": len(verified),
        "repositories": verified,
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
