#!/usr/bin/env python3
"""HIVE v1.15 review evidence pack smoke script."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.getenv("HIVE_BASE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app").rstrip("/")
TOKEN = os.getenv("ADMIN_BEARER_TOKEN", "")


def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {"status": response.status, "data": json.loads(raw) if raw else None}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except Exception:
            data = {"raw": raw}
        return {"status": exc.code, "data": data}


def main() -> None:
    if not TOKEN:
        raise SystemExit("Set ADMIN_BEARER_TOKEN first.")
    created = request("POST", "/v1/execution-reviews", {
        "task": "review podcast SEO workflow evidence pack",
        "repo": "AIMS",
        "workflow_preset": "podcast_episode_review",
        "requested_by": "v1.16-smoke",
        "dry_run": False,
    })
    print("CREATE", json.dumps(created, indent=2))
    plan_id = (created.get("data") or {}).get("plan_id")
    if not plan_id:
        raise SystemExit("No plan_id returned.")
    decision = request("POST", f"/v1/execution-reviews/{plan_id}/decision", {
        "decision": "needs_changes",
        "reviewer": "v1.16-smoke",
        "note": "Evidence-pack smoke test decision.",
    })
    print("DECISION", json.dumps(decision, indent=2))
    audit = request("GET", f"/v1/execution-reviews/{plan_id}/audit-trail")
    print("AUDIT", json.dumps(audit, indent=2))
    pack = request("GET", f"/v1/execution-reviews/{plan_id}/evidence-pack")
    print("PACK", json.dumps(pack, indent=2)[:4000])
    export = request("POST", f"/v1/execution-reviews/{plan_id}/export", {"format": "markdown"})
    print("EXPORT", json.dumps(export, indent=2)[:4000])


if __name__ == "__main__":
    main()
