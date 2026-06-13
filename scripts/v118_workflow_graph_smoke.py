#!/usr/bin/env python3
"""HIVE v1.18 workflow graph smoke script."""
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


print(json.dumps(call("GET", "/v1/workflow-graphs/templates"), indent=2))
print(json.dumps(call("POST", "/v1/workflow-graphs/build", {
    "task": "Review podcast SEO workflow and propose a safe plan.",
    "repo": "AIMS",
    "workflow_preset": "podcast_episode_review",
    "limit": 5,
}), indent=2))
