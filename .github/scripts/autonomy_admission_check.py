#!/usr/bin/env python3
"""Publish non-forgeable, exact-head evidence for trusted autonomous admission."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

API = "https://api.github.com"
CHECK_NAME = "Trusted autonomy admission"
CHECKS_APP_SLUG = "github-actions"


def _request(method: str, path: str, data: dict[str, Any] | None = None) -> Any:
    token = os.environ.get("ADMISSION_TOKEN", "")
    repo = os.environ.get("REPO", "")
    if not token or not repo:
        raise RuntimeError("ADMISSION_TOKEN and REPO are required to publish trusted admission evidence")
    url = path if path.startswith("http") else f"{API}{path}"
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"GitHub admission check API HTTP {exc.code}: {body[:500]}") from exc


def set_admission_check(
    number: int,
    sha: str,
    conclusion: str,
    summary: str,
    *,
    create_if_missing: bool = True,
) -> bool:
    """Create/update trusted evidence for exactly this PR head; never trusts the label."""
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha.lower()):
        raise ValueError("Admission check requires a full 40-character commit SHA")
    if conclusion not in {"success", "failure"}:
        raise ValueError("Admission check conclusion must be success or failure")

    repo = os.environ["REPO"]
    encoded_name = urllib.parse.quote(CHECK_NAME, safe="")
    runs = _request(
        "GET",
        f"/repos/{repo}/commits/{sha}/check-runs?check_name={encoded_name}&filter=all&per_page=100",
    )
    owned = [
        run for run in (runs or {}).get("check_runs", [])
        if (run.get("app") or {}).get("slug") == CHECKS_APP_SLUG
    ]
    owned.sort(key=lambda run: run.get("started_at") or run.get("created_at") or "", reverse=True)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": now,
        "completed_at": now,
        "external_id": f"trusted-autonomy-admission:{number}:{sha}",
        "details_url": f"https://github.com/{repo}/pull/{number}",
        "output": {
            "title": "Trusted admission verified" if conclusion == "success" else "Trusted admission withheld",
            "summary": summary[:65000],
        },
    }
    if owned:
        _request("PATCH", f"/repos/{repo}/check-runs/{owned[0]['id']}", payload)
        return True
    if not create_if_missing:
        return False
    _request("POST", f"/repos/{repo}/check-runs", payload)
    return True
