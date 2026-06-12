from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("HIVE_BASE_URL", "https://liable-loreen-jonathanharris-57884580.koyeb.app").rstrip("/")
TOKEN = os.environ.get("ADMIN_BEARER_TOKEN", "")


def call(path: str) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        headers={"Authorization": f"Bearer {TOKEN}"} if TOKEN else {},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


def show(label: str, payload: dict) -> None:
    print("\n" + "=" * 80)
    print(label)
    print("=" * 80)
    print(json.dumps(payload, indent=2)[:5000])


def main() -> None:
    q = urllib.parse.quote("RSS rewrite")
    show("health", call("/health"))
    show("skills search RSS rewrite", call(f"/v1/skills/search?q={q}&limit=10"))
    show("skills get S194", call("/v1/skills/get?id=S194"))
    show("skills by repo AIMS", call("/v1/skills/by-repo?repo=AIMS&limit=10"))
    show("skills by risk high", call("/v1/skills/by-risk?risk=high&limit=10"))
    show("skills by lane SEO", call("/v1/skills/by-lane?lane=SEO%2FAEO%2FGEO&limit=10"))


if __name__ == "__main__":
    main()
