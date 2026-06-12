#!/usr/bin/env python3
"""HIVE v1.14 execution-review queue smoke test.

Standard-library only. Safe for ReqBin/Trinket/local use. Creates a dry-run plan
by default, then lists the review queue. Set HIVE_CREATE_LIVE_REVIEW=true to
store one D1 review record, and HIVE_APPROVE_CREATED_REVIEW=true to record an
approval decision. Even approved plans remain non-executing.
"""

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.getenv("HIVE_BASE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app")
BEARER = os.getenv("ADMIN_BEARER_TOKEN") or os.getenv("HIVE_BEARER") or "ppqkTWPgnEmeJUwXgwLzHBPlkQuUiBXz"
TIMEOUT = int(os.getenv("HIVE_TEST_TIMEOUT", "90"))
LIVE = os.getenv("HIVE_CREATE_LIVE_REVIEW", "false").lower() == "true"
APPROVE = os.getenv("HIVE_APPROVE_CREATED_REVIEW", "false").lower() == "true"

HEADERS = {"Authorization": f"Bearer {BEARER}", "Content-Type": "application/json"}


def request(method, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=HEADERS, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "elapsed": round(time.time() - started, 2), "data": json.loads(raw) if raw else None}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw}
        return {"status": exc.code, "elapsed": round(time.time() - started, 2), "data": body}


def show(name, result):
    print("\n" + "=" * 80)
    print(name)
    print("=" * 80)
    print(json.dumps(result, indent=2))
    return result.get("status") == 200 and isinstance(result.get("data"), dict) and result["data"].get("ok") is True


created = request("POST", "/v1/execution-reviews", {
    "task": "Review podcast SEO workflow and suggest the safest next action.",
    "repo": "AIMS",
    "workflow_preset": "podcast_episode_review",
    "requested_by": "v1.14-smoke",
    "limit": 5,
    "dry_run": not LIVE,
})
created_ok = show("1. Create execution review plan", created)

listed = request("GET", "/v1/execution-reviews?limit=10")
listed_ok = show("2. List execution review plans", listed)

plan_id = None
if LIVE and created_ok:
    plan_id = created["data"].get("plan_id")
    detail = request("GET", f"/v1/execution-reviews/{plan_id}")
    show("3. Get created execution review plan", detail)
    if APPROVE:
        decision = request("POST", f"/v1/execution-reviews/{plan_id}/decision", {
            "decision": "approved",
            "reviewer": "v1.14-smoke",
            "note": "Smoke approval; still no execution.",
        })
        show("4. Record non-executing approval decision", decision)

print("\nSUMMARY")
print(json.dumps({"create_ok": created_ok, "list_ok": listed_ok, "live": LIVE, "plan_id": plan_id}, indent=2))
