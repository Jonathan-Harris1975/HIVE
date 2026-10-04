#!/usr/bin/env python3
"""Create pull requests for approved development branches without merge authority.

This script is executed only by the trusted default-branch controller. It
never checks out or executes code from the pushed branch, never enables native
auto-merge, and never merges a pull request. Mergify is the sole automated
merge arbiter after trusted admission.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ.get("REPO") or os.environ["GITHUB_REPOSITORY"]
DEFAULT_BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
REPAIR_APP_LOGIN = os.environ.get("REPAIR_APP_LOGIN", "")
MANAGED_LABEL = "automation:branch-pr"
ALLOWED_PREFIXES = ("fix/", "feat/", "chore/", "ci/", "work/", "codex/")
EXCLUDED_PREFIXES = (
    "autonomy/",
    "renovate/",
    "dependabot/",
    "mergify/",
    "tmp/",
    "temp/",
    "internal/",
)
BRANCH_RE = re.compile(r"^(fix|feat|chore|ci|work|codex)/[A-Za-z0-9._/-]+$")


def log(message: str) -> None:
    print(message, flush=True)


@dataclass
class ApiError(RuntimeError):
    status: int
    body: str

    def __str__(self) -> str:
        return f"GitHub API HTTP {self.status}: {self.body[:500]}"


def request(
    method: str,
    path: str,
    data: Any | None = None,
    expected: tuple[int, ...] = (200,),
) -> Any:
    url = path if path.startswith("http") else API + path
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if response.status not in expected:
                raise ApiError(response.status, raw.decode("utf-8", "replace"))
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise ApiError(exc.code, body) from exc


def get(path: str) -> Any:
    return request("GET", path)


def post(path: str, data: Any | None = None, expected: tuple[int, ...] = (200, 201)) -> Any:
    return request("POST", path, data=data, expected=expected)




def event_payload() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}



def ensure_label() -> None:
    try:
        post(
            f"/repos/{REPO}/labels",
            {
                "name": MANAGED_LABEL,
                "color": "1D76DB",
                "description": "PR created and managed by trusted branch automation",
            },
            expected=(201,),
        )
    except ApiError as exc:
        if exc.status != 422:
            raise


def add_label(number: int) -> None:
    post(
        f"/repos/{REPO}/issues/{number}/labels",
        {"labels": [MANAGED_LABEL]},
        expected=(200,),
    )


def list_open_prs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, 11):
        chunk = get(f"/repos/{REPO}/pulls?state=open&per_page=100&page={page}")
        items.extend(chunk)
        if len(chunk) < 100:
            return items
    raise RuntimeError("More than 1,000 open PRs; refusing incomplete reconciliation")


def same_repo(pr: dict[str, Any]) -> bool:
    return str(((pr.get("head") or {}).get("repo") or {}).get("full_name", "")) == REPO


def allowed_branch(branch: str) -> bool:
    if branch == DEFAULT_BRANCH or any(branch.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return False
    if not any(branch.startswith(prefix) for prefix in ALLOWED_PREFIXES):
        return False
    if ".." in branch or branch.endswith("/") or "//" in branch:
        return False
    return BRANCH_RE.fullmatch(branch) is not None


def exact_open_pr(open_prs: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    matches = [
        pr
        for pr in open_prs
        if same_repo(pr)
        and pr.get("head", {}).get("ref") == branch
        and pr.get("base", {}).get("ref") == DEFAULT_BRANCH
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"Multiple open PRs unexpectedly exist for {branch} -> {DEFAULT_BRANCH}; refusing to choose"
        )
    return matches[0] if matches else None


def branch_signal_candidate() -> tuple[str, str] | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_run":
        return None
    payload = event_payload()
    if payload.get("action") != "completed":
        return None
    run = payload.get("workflow_run") or {}
    if run.get("name") != "Branch PR signal":
        return None
    if run.get("event") != "push" or run.get("conclusion") != "success":
        return None
    head_repo = str((run.get("head_repository") or {}).get("full_name", ""))
    branch = str(run.get("head_branch") or "")
    sha = str(run.get("head_sha") or "")
    if head_repo != REPO or not allowed_branch(branch) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        log(f"Ignoring non-managed branch signal for {head_repo}:{branch}@{sha[:12]}.")
        return None

    encoded = urllib.parse.quote(branch, safe="")
    try:
        current_branch = get(f"/repos/{REPO}/branches/{encoded}")
    except ApiError as exc:
        if exc.status == 404:
            log(f"Branch {branch} was deleted; no PR is required.")
            return None
        raise
    current_sha = str(current_branch.get("commit", {}).get("sha", ""))
    if current_sha != sha:
        log(
            f"Branch {branch} moved from signalled {sha[:12]} to {current_sha[:12]}; "
            "a newer signal will reconcile it."
        )
        return None

    comparison = get(
        f"/repos/{REPO}/compare/"
        f"{urllib.parse.quote(DEFAULT_BRANCH, safe='')}...{urllib.parse.quote(branch, safe='')}"
    )
    if int(comparison.get("ahead_by", 0)) <= 0:
        log(f"Branch {branch} has no commits ahead of {DEFAULT_BRANCH}; no PR is required.")
        return None
    return branch, sha


def pr_metadata(branch: str, sha: str) -> tuple[str, str]:
    commit = get(f"/repos/{REPO}/commits/{sha}")
    message = str(commit.get("commit", {}).get("message") or "").strip()
    first, _, rest = message.partition("\n")
    title = first.strip() or branch.replace("/", ": ", 1)
    title = title[:240]

    details = rest.strip()
    if details:
        details = details[:4000]
        details_block = f"\n\n### Commit details\n\n{details}"
    else:
        details_block = ""

    body = (
        "Created automatically by the repository's trusted branch automation.\n\n"
        f"- Source branch: `{branch}`\n"
        f"- Target branch: `{DEFAULT_BRANCH}`\n"
        f"- Signalled head: `{sha}`\n\n"
        "The repository's normal pull-request CI and security workflows must complete "
        "successfully. Trusted automation may then admit the PR to Mergify; Mergify is "
        "the sole automated merger. This branch controller never enables native auto-merge."
        f"{details_block}"
    )
    return title, body


def create_or_reuse_pr(candidate: tuple[str, str] | None = None) -> None:
    candidate = candidate or branch_signal_candidate()
    if candidate is None:
        return
    branch, sha = candidate
    open_prs = list_open_prs()
    existing = exact_open_pr(open_prs, branch)
    if existing is not None:
        if existing.get("user", {}).get("login") == REPAIR_APP_LOGIN:
            add_label(int(existing["number"]))
        log(f"Using existing PR #{existing['number']} for {branch} -> {DEFAULT_BRANCH}.")
        return

    title, body = pr_metadata(branch, sha)
    try:
        created = post(
            f"/repos/{REPO}/pulls",
            {
                "title": title,
                "head": branch,
                "base": DEFAULT_BRANCH,
                "body": body,
                "maintainer_can_modify": True,
            },
            expected=(201,),
        )
    except ApiError as exc:
        if exc.status != 422:
            raise
        existing = exact_open_pr(list_open_prs(), branch)
        if existing is None:
            raise
        if existing.get("user", {}).get("login") == REPAIR_APP_LOGIN:
            add_label(int(existing["number"]))
        log(
            f"PR creation raced with another actor; using existing PR "
            f"#{existing['number']} for {branch}."
        )
        return

    number = int(created["number"])
    add_label(number)
    log(f"Created managed PR #{number} for {branch} -> {DEFAULT_BRANCH}.")


def recover_branch_signals() -> None:
    """Reconcile approved branches after missed/overwritten workflow_run events."""
    if os.environ.get("GITHUB_EVENT_NAME") not in {"schedule", "workflow_dispatch"}:
        return
    for page in range(1, 11):
        branches = get(f"/repos/{REPO}/branches?per_page=100&page={page}")
        for branch in branches:
            name = str(branch.get("name", ""))
            if not allowed_branch(name):
                continue
            sha = str((branch.get("commit") or {}).get("sha", ""))
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                continue
            if exact_open_pr(list_open_prs(), name):
                create_or_reuse_pr((name, sha))
                continue
            # A deliberately closed PR is an owner decision, not a lost signal.
            query = urllib.parse.urlencode({"state": "closed", "base": DEFAULT_BRANCH, "head": f"{REPO.split('/')[0]}:{name}", "per_page": 100})
            closed = get(f"/repos/{REPO}/pulls?{query}")
            if any(same_repo(pr) and pr.get("head", {}).get("ref") == name
                   and pr.get("head", {}).get("sha") == sha
                   and not pr.get("merged_at") and not pr.get("merged") for pr in closed):
                continue
            comparison = get(f"/repos/{REPO}/compare/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}...{urllib.parse.quote(sha, safe='')}")
            if int(comparison.get("ahead_by", 0)) > 0:
                create_or_reuse_pr((name, sha))
        if len(branches) < 100:
            return
    raise RuntimeError("Too many branches; refusing incomplete signal recovery")


def main() -> int:
    if not re.fullmatch(r"[A-Za-z0-9-]+\[bot\]", REPAIR_APP_LOGIN):
        raise RuntimeError("REPAIR_APP_LOGIN must identify the trusted repository GitHub App bot")

    ensure_label()
    create_or_reuse_pr()
    recover_branch_signals()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"::error::{exc}", flush=True)
        raise

