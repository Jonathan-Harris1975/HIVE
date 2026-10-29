"""Restore the missing current-week overview using the deployed HIVE runtime.

No model calls, repair dispatches, publications or evidence cleanup are performed.
Credentials stay in the runtime and only a compact outcome is printed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def restore() -> dict[str, object]:
    from dotenv import dotenv_values

    defaults = Path(__file__).resolve().parents[1] / "HIVE-PRODUCTION-SHARED.env"
    for name, value in dotenv_values(defaults).items():
        if value is not None:
            os.environ.setdefault(name, value)
    from app.core.config import get_settings
    from app.services.lifecycle_reports import generate_and_store_weekly_report, read_latest_weekly_report
    from app.storage.r2 import R2Storage

    now = dt.datetime.now(dt.timezone.utc).isocalendar()
    storage = R2Storage(get_settings())
    try:
        key, report = read_latest_weekly_report(storage, now.year, now.week)
        restored = False
    except FileNotFoundError:
        result = generate_and_store_weekly_report(storage, now.year, now.week, preserve_evidence=True)
        key, report = read_latest_weekly_report(storage, now.year, now.week)
        if result.get("deleted_evidence_objects") != 0:
            raise RuntimeError("Report recovery must preserve source evidence")
        restored = True
    rows = report.get("repositories", [])
    if not isinstance(rows, list) or len(rows) != 8:
        raise RuntimeError("Weekly report must cover all eight repositories")
    return {
        "event": "operations.weekly_report.verified", "restored": restored,
        "report_id": report.get("report_id"), "key": key,
        "overall_status": report.get("overall_status"),
        "evidence_complete": report.get("evidence_complete"),
        "repositories": [{"repository": row.get("repository"), "status": row.get("status"),
                          "evidence_issue": row.get("evidence_issue")} for row in rows],
    }


if __name__ == "__main__":
    try:
        print(json.dumps(restore(), separators=(",", ":")))
    except Exception as exc:
        response = getattr(exc, "response", {})
        code = str(response.get("Error", {}).get("Code", "")) if isinstance(response, dict) else ""
        print(json.dumps({"event": "operations.weekly_report.failed", "error_type": type(exc).__name__,
                          "provider_code": code}), file=sys.stderr)
        raise SystemExit(1) from None
