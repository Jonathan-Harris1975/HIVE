from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "inspect_live_council.py"
SPEC = importlib.util.spec_from_file_location("inspect_live_council", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_council_summary_reports_http_status_without_response_body():
    status = {
        "ok": False,
        "fresh": True,
        "period": "2026-10",
        "completion_status": "degraded",
        "downstream_sync_enabled": True,
        "downstream_sync_ok": False,
        "reason": "latest AI Council run is degraded",
    }
    history = {
        "runs": [
            {
                "run_id": "run-1",
                "completion_status": "degraded",
                "completed_at": "2026-10-01T08:00:00Z",
                "downstream_sync": {
                    "enabled": True,
                    "ok": False,
                    "error": "HTTP 401: confidential response body",
                },
            }
        ]
    }

    output = "\n".join(module.summarize(status, history))

    assert "downstream_http_status=401" in output
    assert "confidential response body" not in output
    assert "run_id=run-1" in output


def test_council_summary_reports_per_target_verification():
    status = {"ok": True, "fresh": True, "period": "2026-10"}
    history = {
        "runs": [
            {
                "run_id": "run-2",
                "completion_status": "completed",
                "downstream_sync": {
                    "enabled": True,
                    "ok": True,
                    "targets": {
                        "aims": {"ok": True, "http_status": 200, "attempt": 1},
                        "rams": {"ok": True, "http_status": 200, "attempt": 2},
                    },
                },
            }
        ]
    }

    output = "\n".join(module.summarize(status, history))

    assert "aims_ok=True aims_http_status=200 aims_attempt=1" in output
    assert "rams_ok=True rams_http_status=200 rams_attempt=2" in output
