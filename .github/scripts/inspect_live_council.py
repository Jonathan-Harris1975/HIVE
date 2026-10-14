#!/usr/bin/env python3
"""Read and summarize live HIVE Council state without starting governance writes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any


MAX_READ_ATTEMPTS = 3
MAX_RETRY_AFTER_SECONDS = 20
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float | None:
    """Return a bounded retry delay, or None when Retry-After exceeds our wait budget."""
    default = min(2 ** (attempt - 1), 4)
    headers = exc.headers
    value = headers.get("Retry-After", "").strip() if headers else ""
    if not value:
        return float(default)
    try:
        delay = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            delay = (retry_at - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return float(default)
    if delay < 0:
        return 0.0
    if delay > MAX_RETRY_AFTER_SECONDS:
        return None
    return delay


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward the admin bearer token to redirected endpoints."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get_json(base: str, token: str, path: str) -> dict[str, Any]:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    for attempt in range(1, MAX_READ_ATTEMPTS + 1):
        opener = urllib.request.build_opener(NoRedirectHandler)
        try:
            with opener.open(request, timeout=25) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_STATUSES or attempt == MAX_READ_ATTEMPTS:
                raise RuntimeError(f"GET {path} failed with HTTP {exc.code}") from None
            delay = _retry_delay(exc, attempt)
            if delay is None:
                raise RuntimeError(f"GET {path} failed with HTTP {exc.code}") from None
            time.sleep(delay)
            continue
        except (TimeoutError, urllib.error.URLError):
            if attempt == MAX_READ_ATTEMPTS:
                raise RuntimeError(f"GET {path} failed with a connection or response error") from None
            time.sleep(min(2 ** (attempt - 1), 4))
            continue
        except ValueError:
            if attempt == MAX_READ_ATTEMPTS:
                raise RuntimeError(f"GET {path} failed with a connection or response error") from None
            time.sleep(min(2 ** (attempt - 1), 4))
            continue
        break
    if not isinstance(payload, dict):
        raise RuntimeError(f"GET {path} returned an invalid response")
    return payload


def downstream_failure_category(error: str, http_status: str | None = None) -> str:
    """Return a safe allowlisted category without exposing downstream response text."""
    normalized = error.lower()
    premium_markers = (
        "premium_justification",
        "premium justification",
        "evaluation_evidence",
        "evaluation evidence",
        "cheaper_model_tested",
        "cheaper model tested",
    )
    if any(marker in normalized for marker in premium_markers):
        return "premium_approval"
    if "readiness" in normalized:
        return "service_readiness"
    if "configuration is missing" in normalized:
        return "configuration"
    if http_status in {"408", "409", "425", "429", "500", "502", "503", "504"}:
        return "downstream_unavailable"
    if http_status in {"400", "422"}:
        return "downstream_validation"
    if http_status in {"401", "403"}:
        return "downstream_authentication"
    return "downstream_error"


def summarize(status: dict[str, Any], history: dict[str, Any]) -> list[str]:
    lines = [
        "Council status:",
        f"  ok={status.get('ok')} fresh={status.get('fresh')} period={status.get('period')}",
        f"  completion_status={status.get('completion_status')} downstream_sync_enabled={status.get('downstream_sync_enabled')} downstream_sync_ok={status.get('downstream_sync_ok')}",
        f"  reason={status.get('reason')}",
    ]
    runs = history.get("runs")
    latest = runs[-1] if isinstance(runs, list) and runs and isinstance(runs[-1], dict) else {}
    sync = latest.get("downstream_sync")
    sync = sync if isinstance(sync, dict) else {}
    lines.extend(
        [
            "Latest Council run:",
            f"  run_id={latest.get('run_id')} completion_status={latest.get('completion_status')}",
            f"  completed_at={latest.get('completed_at') or latest.get('occurred_at')}",
            f"  sync_enabled={sync.get('enabled')} sync_ok={sync.get('ok')}",
        ]
    )
    error = str(sync.get("error") or "")
    status_match = re.search(r"\bHTTP\s+(\d{3})\b", error, flags=re.IGNORECASE)
    if status_match:
        http_status = status_match.group(1)
        lines.append(f"  downstream_http_status={http_status}")
        lines.append(
            f"  downstream_failure_category={downstream_failure_category(error, http_status)}"
        )
    elif error:
        lines.append(
            f"  downstream_failure_category={downstream_failure_category(error)}"
        )
    targets = sync.get("targets")
    if isinstance(targets, dict):
        for name in ("aims", "rams"):
            target = targets.get(name)
            if isinstance(target, dict):
                lines.append(
                    f"  {name}_ok={target.get('ok')} {name}_http_status={target.get('http_status')} {name}_attempt={target.get('attempt')}"
                )
    return lines


def main() -> int:
    base = os.environ.get("HIVE_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("HIVE_ADMIN_TOKEN", "")
    if not base.startswith("https://") or not token:
        print("HIVE_BASE_URL (https) and HIVE_ADMIN_TOKEN are required", file=sys.stderr)
        return 2
    try:
        status = get_json(base, token, "/v1/ai-council/status")
        history = get_json(base, token, "/v1/ai-council/history?limit=5")
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("\n".join(summarize(status, history)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
