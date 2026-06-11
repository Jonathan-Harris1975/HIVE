#!/usr/bin/env python3
"""HIVE v1.6 smoke checks.

Environment variables:
  HIVE_URL              default: https://liable-loreen-jonathanharris-57884580.koyeb.app
  ADMIN_BEARER_TOKEN    required for /v1 endpoints
  HIVE_TEST_OBJECT_KEY  optional; when set, runs a dry-run workflow-preset chat check
  HIVE_WORKFLOW_PRESET  optional; default audit_report_review
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv("HIVE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app").rstrip("/")
TOKEN = os.getenv("ADMIN_BEARER_TOKEN", "")
TEST_OBJECT_KEY = os.getenv("HIVE_TEST_OBJECT_KEY", "")
WORKFLOW_PRESET = os.getenv("HIVE_WORKFLOW_PRESET", "audit_report_review")
TIMEOUT = float(os.getenv("HIVE_SMOKE_TIMEOUT_SECONDS", "45"))


def request_json(method: str, path: str, payload: dict | None = None, auth: bool = False) -> dict:
    url = BASE_URL + path
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    started = time.perf_counter()
    req = urllib.request.Request(url, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "data": json.loads(raw) if raw else None,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return {"ok": False, "status": exc.code, "elapsed_seconds": round(time.perf_counter() - started, 3), "data": body}
    except Exception as exc:
        return {
            "ok": False,
            "status": "EXCEPTION",
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "data": {"type": type(exc).__name__, "error": str(exc)},
        }


def show(name: str, result: dict) -> bool:
    print("\n" + "=" * 78)
    print(name)
    print("=" * 78)
    print(json.dumps(result, indent=2))
    passed = bool(result.get("ok") and (result.get("data") or {}).get("ok") is True)
    print("PASS" if passed else "FAIL")
    return passed


def main() -> int:
    checks: list[tuple[str, dict]] = []
    checks.append(("1. /health", request_json("GET", "/health")))
    checks.append(("2. /healthz", request_json("GET", "/healthz")))

    if not TOKEN:
        print("\nADMIN_BEARER_TOKEN is not set; authenticated v1.6 checks skipped.")
        return 0 if all(show(name, result) for name, result in checks) else 1

    checks.append(("3. /v1/workflow-presets", request_json("GET", "/v1/workflow-presets", auth=True)))
    checks.append(("4. /v1/files/r2-lanes", request_json("GET", "/v1/files/r2-lanes", auth=True)))

    if TEST_OBJECT_KEY:
        checks.append((
            "5. /v1/chat/with-file preset dry run",
            request_json(
                "POST",
                "/v1/chat/with-file",
                auth=True,
                payload={
                    "object_key": TEST_OBJECT_KEY,
                    "message": "Summarise the key operational findings and return retrieval evidence.",
                    "workflow_preset": WORKFLOW_PRESET,
                    "dry_run": True,
                    "test_run_id": "v16-smoke",
                },
            ),
        ))

    passed = [show(name, result) for name, result in checks]
    print("\nSUMMARY")
    print(f"Passed: {sum(1 for item in passed if item)}")
    print(f"Failed: {sum(1 for item in passed if not item)}")
    return 0 if all(passed) else 1


if __name__ == "__main__":
    sys.exit(main())
