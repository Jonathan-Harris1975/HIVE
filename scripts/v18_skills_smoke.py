from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("HIVE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app").rstrip("/")
TOKEN = os.environ.get("ADMIN_BEARER_TOKEN", "")

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"} if TOKEN else {"Content-Type": "application/json"}


def request(method: str, path: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(BASE_URL + path, data=body, method=method, headers=HEADERS)
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


def show(name: str, result: dict) -> None:
    print("\n" + "=" * 80)
    print(name)
    print("=" * 80)
    print(json.dumps(result, indent=2))


show("1. Skills status", request("GET", "/v1/skills/status"))
show("2. Import dry run", request("POST", "/v1/skills/import-manifest", {"dry_run": True}))
show("3. Import live", request("POST", "/v1/skills/import-manifest", {"dry_run": False}))
show("4. Categories", request("GET", "/v1/skills/categories"))
show("5. Search audit", request("GET", "/v1/skills/search?q=audit&limit=10"))
show("6. RAMS list", request("GET", "/v1/skills/list?repo=RAMS&limit=10"))
