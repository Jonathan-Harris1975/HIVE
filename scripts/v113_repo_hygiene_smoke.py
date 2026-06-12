#!/usr/bin/env python3
"""HIVE v1.13 repo hygiene smoke test.

Usage:
  ADMIN_BEARER_TOKEN=... python scripts/v113_repo_hygiene_smoke.py
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.getenv("HIVE_BASE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app").rstrip("/")
TOKEN = os.getenv("ADMIN_BEARER_TOKEN", "")


def get_json(path: str) -> dict[str, object]:
    req = urllib.request.Request(
        BASE_URL + path,
        method="GET",
        headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return {"status": response.status, "data": json.loads(response.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {"raw": body}
        return {"status": exc.code, "data": parsed}


def main() -> None:
    health = get_json("/health")
    hygiene = get_json("/v1/system/repo-hygiene?include_hashes=true&max_files=5000")
    print(json.dumps({"health": health, "repo_hygiene": hygiene}, indent=2))
    if health.get("status") != 200 or not health.get("data", {}).get("ok"):
        raise SystemExit(1)
    if hygiene.get("status") != 200 or not hygiene.get("data", {}).get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
