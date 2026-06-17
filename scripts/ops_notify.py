#!/usr/bin/env python3
"""Send a bounded operational event to the HIVE event inbox."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any


def _clean(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value.startswith("{{") and "secret." in value:
        return ""
    return value


def send_event(event: dict[str, Any]) -> bool:
    url = _clean("OPS_ALERT_WEBHOOK_URL")
    token = _clean("OPS_ALERT_WEBHOOK_TOKEN")
    if not url or not token:
        print("Operational alert webhook is not configured; skipping.")
        return False
    payload = {
        "environment": "production",
        "occurred_at": datetime.now(UTC).isoformat(),
        **event,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "HIVE-ecosystem-deployment-watcher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=float(os.getenv("OPS_ALERT_TIMEOUT_SECONDS", "8"))) as response:
            ok = 200 <= response.status < 300
            print(f"Operational event delivery status: {response.status}")
            return ok
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"Operational event delivery failed: {exc.__class__.__name__}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--source", default="github_actions")
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--severity", default="critical", choices=["info", "warning", "critical"])
    parser.add_argument("--event-id", default="")
    parser.add_argument("--release-id", default="")
    parser.add_argument("--url", default="")
    args = parser.parse_args()
    event_id = args.event_id or f"{args.source}:{args.service}:{os.getenv('GITHUB_RUN_ID', datetime.now(UTC).isoformat())}"
    send_event(
        {
            "event_id": event_id,
            "source": args.source,
            "service": args.service,
            "severity": args.severity,
            "event_type": args.event_type,
            "title": args.title,
            "summary": args.summary,
            "release_id": args.release_id or os.getenv("GITHUB_SHA") or None,
            "url": args.url or None,
            "details": {
                "repository": os.getenv("GITHUB_REPOSITORY"),
                "workflow": os.getenv("GITHUB_WORKFLOW"),
                "runId": os.getenv("GITHUB_RUN_ID"),
                "runAttempt": os.getenv("GITHUB_RUN_ATTEMPT"),
            },
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
