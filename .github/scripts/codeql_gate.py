#!/usr/bin/env python3
"""Block only current, high-impact CodeQL security findings.

GitHub's native code-scanning result check remains an independent PR gate.  The
extra workflow gate must not turn old, low-severity or quality alerts into a
failure for an unrelated pull request.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ["GH_TOKEN"]
PR = os.environ.get("PR_NUMBER", "")
BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def api(path: str) -> list[dict]:
    """Get every page; never silently pass when a scan cannot be read."""
    url = "https://api.github.com" + path
    items: list[dict] = []
    while url:
        request = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(request, timeout=30) as response:
            page = json.load(response)
            if not isinstance(page, list):
                raise ValueError("Expected a list of CodeQL results")
            items.extend(page)
            link = response.headers.get("Link", "")
            url = next((x.split(";", 1)[0].strip(" <>") for x in link.split(",")
                        if 'rel="next"' in x), "")
    return items


def added_lines(patch: str) -> set[int]:
    lines: set[int] = set()
    current = None
    for line in patch.splitlines():
        match = HUNK.match(line)
        if match:
            current = int(match.group(1))
        elif current is not None and line.startswith("+") and not line.startswith("+++"):
            lines.add(current)
            current += 1
        elif current is not None and line.startswith(" "):
            current += 1
    return lines


def blocking_security(alert: dict) -> bool:
    if alert.get("state") != "open" or alert.get("tool", {}).get("name") != "CodeQL":
        return False
    rule = alert.get("rule") or {}
    tags = [str(tag).lower() for tag in rule.get("tags", [])]
    score = rule.get("security_severity")
    if isinstance(score, (int, float)) or (isinstance(score, str) and re.fullmatch(r"\d+(?:\.\d+)?", score)):
        return float(score) >= 7
    level = str(rule.get("security_severity_level", "")).lower()
    if level in {"high", "critical"}:
        return True
    return "security" in tags and str(rule.get("severity", "")).lower() in {"error", "critical", "high"}


def main() -> int:
    ref = f"refs/pull/{int(PR)}/merge" if PR else f"refs/heads/{BRANCH}"
    scope = "ref=" + urllib.parse.quote(ref, safe="")
    alerts = api(f"/repos/{REPO}/code-scanning/alerts?state=open&{scope}&per_page=100")
    qualifying = [item for item in alerts if blocking_security(item)]
    uncertain = 0
    if PR:
        files = api(f"/repos/{REPO}/pulls/{int(PR)}/files?per_page=100")
        changed = {item["filename"]: added_lines(item["patch"]) for item in files if "patch" in item}
        selected = []
        for item in qualifying:
            location = (item.get("most_recent_instance") or {}).get("location") or {}
            path, start = location.get("path"), location.get("start_line")
            if path not in changed:
                uncertain += 1
            elif isinstance(start, int) and start in changed[path]:
                selected.append(item)
        qualifying = selected

    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write("## CodeQL security findings\n\n")
        summary.write(f"Blocking high/critical security findings: **{len(qualifying)}**.\n\n")
        for item in qualifying[:30]:
            rule = item.get("rule") or {}
            location = (item.get("most_recent_instance") or {}).get("location") or {}
            summary.write(f"- #{item['number']} `{rule.get('id', 'unknown')}` "
                          f"`{location.get('path', '?')}:{location.get('start_line', '?')}` "
                          f"{item.get('html_url', '')}\n")
        if uncertain:
            summary.write(f"\n{uncertain} findings could not be tied to added PR lines here; "
                          "GitHub's native code-scanning PR check evaluates those independently.\n")
    if qualifying:
        print(f"::error::CodeQL found {len(qualifying)} current high/critical security findings")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"::error::CodeQL classification unavailable: {exc}", file=sys.stderr)
        sys.exit(1)
