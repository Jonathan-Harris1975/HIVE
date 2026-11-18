#!/usr/bin/env python3
"""Dispatch CI once for a current main commit with no exact-SHA CI run.

Trusted default-branch code only. A failed or pending CI run is evidence and is
never retried here; this repairs only GitHub's suppressed merge push events.
"""
import json
import os
import urllib.parse
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
CI_NAME = os.environ.get("MAIN_CI_NAME", "")


def api(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", "Bearer " + TOKEN)
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


def recover():
    if not REPO or not TOKEN or not CI_NAME:
        raise RuntimeError("GITHUB_REPOSITORY, GITHUB_TOKEN and MAIN_CI_NAME are required")
    sha = api("GET", f"/repos/{REPO}/git/ref/heads/main")["object"]["sha"]
    for event in ("push", "workflow_dispatch"):
        query = urllib.parse.urlencode({"head_sha": sha, "event": event, "per_page": 100})
        runs = api("GET", f"/repos/{REPO}/actions/runs?{query}")
        if runs["total_count"] > len(runs["workflow_runs"]):
            # Never infer an absent gate from a partial run listing.
            print(f"Main {sha}: {event} workflow listing incomplete; no dispatch")
            return False
        if any(run["name"] == CI_NAME for run in runs["workflow_runs"]):
            print(f"Main {sha}: exact-commit CI already recorded; no dispatch")
            return False
    # A race can move main during the read. A dispatched run checks out the new
    # main and is assessed against its own exact SHA, never the previous one.
    api("POST", f"/repos/{REPO}/actions/workflows/ci.yml/dispatches", {"ref": "main"})
    print(f"Dispatched {CI_NAME} for unevaluated main {sha}; release remains pending")
    return True


if __name__ == "__main__":
    recover()
