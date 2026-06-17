#!/usr/bin/env python3
"""Poll the latest Koyeb production deployment and alert on terminal failure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable

from ops_notify import send_event

SUCCESS = {"healthy", "sleeping"}
FAILURE = {"error", "failed", "unhealthy", "cancelled", "canceled"}
PENDING = {"pending", "provisioning", "scheduled", "allocating", "starting", "stopping", "building", "deploying", "degraded"}


def _walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if "status" in value and any(key in value for key in ("id", "created_at", "createdAt", "name")):
            yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _created(item: dict[str, Any]) -> str:
    return str(item.get("created_at") or item.get("createdAt") or item.get("updated_at") or item.get("updatedAt") or "")


def _deployments(service: str, token: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        ["koyeb", "deployments", "list", "--service", service, "--token", token, "-o", "json"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=45,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Koyeb CLI failed with exit code {result.returncode}")
    payload = json.loads(result.stdout)
    deployments = list(_walk(payload))
    deployments.sort(key=_created, reverse=True)
    return deployments


def _status(item: dict[str, Any]) -> str:
    value = item.get("status")
    if isinstance(value, dict):
        value = value.get("status") or value.get("state") or value.get("name")
    return str(value or "unknown").strip().lower()


def _parse_timestamp(value: str) -> datetime | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _deployment_sha(item: dict[str, Any]) -> str:
    candidates = {"commit_sha", "commitSha", "git_sha", "gitSha", "sha", "revision"}
    stack: list[Any] = [item]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key in candidates and isinstance(child, str) and len(child.strip()) >= 7:
                    return child.strip()
                stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)
    return ""


def _matches_expected_deployment(item: dict[str, Any], expected_sha: str, expected_after: datetime | None) -> bool:
    candidate_sha = _deployment_sha(item)
    if expected_sha and candidate_sha and not candidate_sha.lower().startswith(expected_sha.lower()):
        return False
    if expected_after is not None:
        created = _parse_timestamp(_created(item))
        if created is not None and created < expected_after - timedelta(minutes=5):
            return False
    return True


def main() -> int:
    service = os.getenv("KOYEB_SERVICE", "").strip()
    token = os.getenv("KOYEB_TOKEN", "").strip()
    display_name = os.getenv("SERVICE_DISPLAY_NAME", service or "Koyeb service").strip()
    if not service or not token:
        print("Koyeb deployment watcher is not configured; skipping.")
        return 0
    attempts = max(1, int(os.getenv("KOYEB_DEPLOYMENT_MAX_ATTEMPTS", "40")))
    poll_seconds = max(5, int(os.getenv("KOYEB_DEPLOYMENT_POLL_SECONDS", "15")))
    expected_sha = os.getenv("EXPECTED_DEPLOYMENT_SHA", os.getenv("GITHUB_SHA", "")).strip()
    expected_after = _parse_timestamp(os.getenv("EXPECTED_DEPLOYMENT_AFTER", ""))
    degraded_seen = 0
    last: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        candidates = _deployments(service, token)
        last = next(
            (item for item in candidates if _matches_expected_deployment(item, expected_sha, expected_after)),
            None,
        )
        if last is None:
            print(f"Expected Koyeb deployment not visible yet ({attempt}/{attempts}).")
            time.sleep(poll_seconds)
            continue
        status = _status(last)
        deployment_id = str(last.get("id") or last.get("name") or "unknown")
        print(f"{display_name} deployment {deployment_id}: {status} ({attempt}/{attempts})")
        if status in SUCCESS:
            return 0
        if status == "degraded":
            degraded_seen += 1
            if degraded_seen < 4:
                time.sleep(poll_seconds)
                continue
        if status in FAILURE or degraded_seen >= 4:
            send_event(
                {
                    "event_id": f"koyeb:{display_name}:{deployment_id}",
                    "source": "koyeb_deployment_watcher",
                    "service": display_name,
                    "severity": "critical" if status != "degraded" else "warning",
                    "event_type": "deployment_failed" if status != "degraded" else "deployment_sustained_degradation",
                    "title": f"{display_name} Koyeb deployment did not become healthy",
                    "summary": f"Deployment {deployment_id} reached status {status}.",
                    "release_id": os.getenv("GITHUB_SHA") or None,
                    "url": f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/{os.getenv('GITHUB_REPOSITORY', '')}/actions/runs/{os.getenv('GITHUB_RUN_ID', '')}",
                    "details": {"deploymentId": deployment_id, "status": status, "serviceReference": service},
                }
            )
            return 1
        if status not in PENDING:
            print(f"Unknown Koyeb status {status}; continuing bounded polling.")
        time.sleep(poll_seconds)

    deployment_id = str((last or {}).get("id") or "not-found")
    status = _status(last or {})
    send_event(
        {
            "event_id": f"koyeb:{display_name}:timeout:{os.getenv('GITHUB_RUN_ID', datetime.now(UTC).isoformat())}",
            "source": "koyeb_deployment_watcher",
            "service": display_name,
            "severity": "warning",
            "event_type": "deployment_watch_timeout",
            "title": f"{display_name} Koyeb deployment confirmation timed out",
            "summary": f"The watcher ended before deployment {deployment_id} became healthy. Last status: {status}.",
            "release_id": os.getenv("GITHUB_SHA") or None,
            "details": {"deploymentId": deployment_id, "status": status, "serviceReference": service},
        }
    )
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Koyeb deployment watcher failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
