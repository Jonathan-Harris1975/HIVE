#!/usr/bin/env python3
"""HIVE v1.19 controlled execution preview smoke script."""
from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.environ.get("HIVE_BASE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app")
TOKEN = os.environ.get("ADMIN_BEARER_TOKEN", "")


def call(method: str, path: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.loads(res.read().decode())


print(json.dumps(call("GET", "/v1/execution-preview/policies"), indent=2))
print(json.dumps(call("POST", "/v1/execution-preview", {
    "task": "Triage a failed RAMS audit and propose a review-gated plan.",
    "repo": "RAMS",
    "workflow_preset": "audit_report_review",
    "approval_state": "pending_review",
    "limit": 5,
}), indent=2))
