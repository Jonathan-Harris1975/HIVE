#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("HIVE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app").rstrip("/")
TOKEN = os.environ.get("ADMIN_BEARER_TOKEN", "")


def call(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return {"status": response.status, "body": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body_json = json.loads(raw)
        except Exception:
            body_json = {"raw": raw}
        return {"status": exc.code, "body": body_json}


def main() -> int:
    checks = [
        ("health", "/health", "GET", None),
        ("skills integrity", "/v1/skills/integrity?limit=500", "GET", None),
        ("skills duplicates", "/v1/skills/duplicates?limit=500", "GET", None),
        ("skills missing", "/v1/skills/missing?limit=500", "GET", None),
        ("skills orphans", "/v1/skills/orphans?limit=500", "GET", None),
        ("skills rebuild dry-run", "/v1/skills/rebuild-index", "POST", {"dry_run": True}),
    ]
    failed = 0
    for name, path, method, body in checks:
        result = call(path, method, body)
        print("\n" + "=" * 80)
        print(name)
        print("=" * 80)
        print(json.dumps(result, indent=2)[:5000])
        payload = result.get("body") or {}
        if result.get("status") != 200 or payload.get("ok") is not True:
            failed += 1
    print("\nSUMMARY:", "PASS" if failed == 0 else f"FAIL ({failed})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
