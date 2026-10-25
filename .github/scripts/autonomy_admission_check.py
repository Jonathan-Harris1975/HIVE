#!/usr/bin/env python3
"""Publish non-forgeable, exact-head evidence for trusted autonomous admission."""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

API = "https://api.github.com"
CHECK_NAME = "Trusted autonomy admission"
CHECKS_APP_SLUG = "github-actions"


def _retry_delay(
    retry_after: str | None,
    reset_at: str | None,
    rate_limit_remaining: str | None,
    attempt: int,
) -> float:
    """Return a bounded delay for delta-seconds or HTTP-date retry headers."""
    if retry_after:
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(retry_after)
            except (TypeError, ValueError, OverflowError):
                retry_at = None
            if retry_at is not None:
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = retry_at.timestamp() - time.time()
            else:
                delay = None
        if delay is not None and math.isfinite(delay):
            return min(30.0, max(0.0, delay))

    if reset_at and rate_limit_remaining == "0":
        try:
            delay = float(reset_at) - time.time() + 1.0
        except (TypeError, ValueError, OverflowError):
            delay = None
        if delay is not None and math.isfinite(delay):
            return min(30.0, max(1.0, delay))

    return min(8.0, 2.0 ** attempt)


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

    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            retryable = exc.code == 429 or exc.code >= 500 or (
                exc.code == 403 and (
                    exc.headers.get("Retry-After")
                    or exc.headers.get("X-RateLimit-Remaining") == "0"
                )
            )
            if not retryable or attempt == 4:
                raise RuntimeError(f"GitHub admission check API HTTP {exc.code}: {body[:500]}") from exc
            retry_after = exc.headers.get("Retry-After")
            reset_at = exc.headers.get("X-RateLimit-Reset")
            delay = _retry_delay(
                retry_after,
                reset_at,
                exc.headers.get("X-RateLimit-Remaining"),
                attempt,
            )
            time.sleep(delay)
        except urllib.error.URLError:
            if attempt == 4:
                raise
            time.sleep(min(8.0, 2.0 ** attempt))


def set_admission_check(
    number: int,
    sha: str,
    conclusion: str,
    summary: str,
    *,
    create_if_missing: bool = True,
) -> bool:
    """Create/update trusted evidence for exactly this PR head; never trusts the label."""
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise ValueError("Admission check requires a full 40-character commit SHA")
    # GitHub returns lowercase SHAs; canonicalize valid case-insensitive hex input.
    sha = sha.lower()
    if conclusion not in {"success", "failure"}:
        raise ValueError("Admission check conclusion must be success or failure")

    repo = os.environ["REPO"]
    encoded_name = urllib.parse.quote(CHECK_NAME, safe="")
    runs = _request(
        "GET",
        f"/repos/{repo}/commits/{sha}/check-runs?check_name={encoded_name}&filter=all&per_page=100",
    )
    external_id = f"trusted-autonomy-admission:{number}:{sha}"
    owned = [
        run for run in (runs or {}).get("check_runs", [])
        if (run.get("app") or {}).get("slug") == CHECKS_APP_SLUG
        and run.get("name") == CHECK_NAME
        and run.get("head_sha") == sha
        and run.get("external_id") == external_id
    ]
    owned.sort(key=lambda run: run.get("started_at") or run.get("created_at") or "", reverse=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "name": CHECK_NAME,
        "head_sha": sha,
        "status": "completed",
        "conclusion": conclusion,
        "started_at": now,
        "completed_at": now,
        "external_id": external_id,
        "details_url": f"https://github.com/{repo}/pull/{number}",
        "output": {
            "title": "Trusted admission verified" if conclusion == "success" else "Trusted admission withheld",
            "summary": summary[:65000],
        },
    }
    if owned:
        _request("PATCH", f"/repos/{repo}/check-runs/{owned[0]['id']}", payload)
    elif not create_if_missing:
        return False
    else:
        _request("POST", f"/repos/{repo}/check-runs", payload)

    # Commit statuses are append-only and GitHub caps each SHA/context at 1,000.
    # Avoid publishing transient preflight failures or repeating an unchanged final state.
    if not summary.startswith("Trusted admission is being re-evaluated;"):
        target_url = f"https://github.com/{repo}/pull/{number}"
        desired_state = "success" if conclusion == "success" else "failure"
        prior_statuses = _request(
            "GET",
            f"/repos/{repo}/commits/{sha}/statuses?per_page=100",
        ) or []
        matching_statuses = [
            status for status in prior_statuses
            if status.get("context") == CHECK_NAME
            and status.get("target_url") == target_url
            and (status.get("creator") or {}).get("login") == "github-actions[bot]"
        ]
        matching_statuses.sort(key=lambda status: status.get("created_at") or "", reverse=True)
        if not matching_statuses or matching_statuses[0].get("state") != desired_state:
            _request(
                "POST",
                f"/repos/{repo}/statuses/{sha}",
                {
                    "state": desired_state,
                    "target_url": target_url,
                    "description": "Trusted admission verified" if conclusion == "success" else "Trusted admission withheld",
                    "context": CHECK_NAME,
                },
            )
    return True
