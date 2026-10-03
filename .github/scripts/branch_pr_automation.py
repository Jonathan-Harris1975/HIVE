#!/usr/bin/env python3
"""Create and safely auto-merge pull requests for approved development branches.

This script is executed only by the trusted default-branch controller. It
never checks out or executes code from the pushed branch.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ.get("REPO") or os.environ["GITHUB_REPOSITORY"]
DEFAULT_BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
REPAIR_APP_LOGIN = os.environ.get("REPAIR_APP_LOGIN", "")
REQUIRED_WORKFLOWS = [
    item.strip()
    for item in os.environ.get("REQUIRED_WORKFLOWS", "").split("|")
    if item.strip()
]

REQUIRED_CHECKS = [x.strip() for x in os.environ.get("REQUIRED_CHECKS", "").split("|") if x.strip()]

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
BLOCKING_LABELS = {
    "autonomy:human-hold",
    "do-not-merge",
    "do not merge",
    "hold",
    "needs-manual-review",
    "autonomy:obsolete",
    "autonomy:superseded",
}
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


def put(path: str, data: Any | None = None, expected: tuple[int, ...] = (200, 202)) -> Any:
    return request("PUT", path, data=data, expected=expected)


def graphql(query: str, variables: dict[str, Any]) -> Any:
    result = request(
        "POST",
        GRAPHQL,
        {"query": query, "variables": variables},
        expected=(200,),
    )
    errors = result.get("errors", []) if isinstance(result, dict) else []
    if errors:
        raise RuntimeError("GitHub GraphQL error: " + json.dumps(errors)[:1000])
    return result.get("data", {}) if isinstance(result, dict) else {}


def event_payload() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def issue_labels(pr: dict[str, Any]) -> set[str]:
    return {str(item.get("name", "")).strip().lower() for item in pr.get("labels", [])}


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
    return str(pr.get("head", {}).get("repo", {}).get("full_name", "")) == REPO


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
        "successfully before native GitHub auto-merge is requested."
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


def latest_pull_request_runs(sha: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for page in range(1, 11):
        query = urllib.parse.urlencode({"head_sha": sha, "event": "pull_request", "per_page": 100, "page": page})
        payload = get(f"/repos/{REPO}/actions/runs?{query}")
        chunk = payload.get("workflow_runs", [])
        for run in chunk:
            name = str(run.get("name", ""))
            old = latest.get(name)
            if old is None or (int(run.get("id", 0)), int(run.get("run_attempt", 1))) > (int(old.get("id", 0)), int(old.get("run_attempt", 1))):
                latest[name] = run
        if len(chunk) < 100:
            return latest
    raise RuntimeError("Too many workflow runs; refusing incomplete CI evaluation")


def required_checks_green(pr: dict[str, Any]) -> tuple[bool, str]:
    sha = str(pr.get("head", {}).get("sha", ""))
    runs = latest_pull_request_runs(sha)
    for name in REQUIRED_WORKFLOWS:
        run = runs.get(name)
        if run is None:
            return False, f"required workflow {name!r} has not run on {sha[:12]}"
        status = str(run.get("status", ""))
        conclusion = str(run.get("conclusion", ""))
        if status != "completed" or conclusion != "success":
            return False, f"required workflow {name!r} is {status}/{conclusion}"
    return True, "all repository-specific required workflows succeeded"


def managed_pr(pr: dict[str, Any]) -> bool:
    return (
        pr.get("state") == "open"
        and not pr.get("draft")
        and same_repo(pr)
        and pr.get("base", {}).get("ref") == DEFAULT_BRANCH
        and allowed_branch(str(pr.get("head", {}).get("ref", "")))
        and pr.get("user", {}).get("login") == REPAIR_APP_LOGIN
        and MANAGED_LABEL.lower() in issue_labels(pr)
        and not issue_labels(pr).intersection(BLOCKING_LABELS)
    )


def refresh_pr(number: int) -> dict[str, Any]:
    return get(f"/repos/{REPO}/pulls/{number}")


def update_branch_if_behind(pr: dict[str, Any]) -> bool:
    if pr.get("mergeable_state") != "behind":
        return False
    number = int(pr["number"])
    sha = str(pr.get("head", {}).get("sha", ""))
    try:
        put(
            f"/repos/{REPO}/pulls/{number}/update-branch",
            {"expected_head_sha": sha},
            expected=(202,),
        )
        log(f"Updated PR #{number} from {DEFAULT_BRANCH}; waiting for fresh exact-head CI.")
    except ApiError as exc:
        if exc.status not in (403, 409, 422):
            raise
        log(f"PR #{number} is behind but GitHub could not update it automatically ({exc.status}).")
    return True


def native_merge_policy() -> tuple[str | None, str]:
    """Require enforced checks; a successful workflow alone is not protection."""
    settings = get(f"/repos/{REPO}")
    if not settings.get("allow_auto_merge"):
        return None, "repository auto-merge is disabled"
    branch = get(f"/repos/{REPO}/branches/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}")
    if not branch.get("protected"):
        return None, f"{DEFAULT_BRANCH} has no enforced branch protection"
    # The public effective-rules endpoint excludes disabled/evaluate rulesets.
    rules = []
    for page in range(1, 11):
        chunk = get(f"/repos/{REPO}/rules/branches/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}?per_page=100&page={page}")
        rules.extend(chunk)
        if len(chunk) < 100:
            break
    else:
        return None, "effective branch rules exceed the safe pagination limit"
    contexts: set[str] = set()
    strict = False
    for rule in rules:
        if rule.get("type") == "merge_queue":
            return None, "native merge queue requires a separately validated merge_group CI path"
        if rule.get("type") == "required_status_checks":
            params = rule.get("parameters") or {}
            contexts.update(x.get("context", "") for x in params.get("required_status_checks", []))
            strict = strict or params.get("strict_required_status_checks_policy") is True
    # Legacy branch protection is not returned by the rulesets endpoint.
    owner, name = REPO.split("/", 1)
    data = graphql("""
      query($owner:String!,$name:String!,$ref:String!) {
        repository(owner:$owner,name:$name) {
          ref(qualifiedName:$ref) {
            branchProtectionRule {
              requiresStatusChecks requiredStatusCheckContexts requiresStrictStatusChecks
            }
          }
        }
      }
    """, {"owner": owner, "name": name, "ref": f"refs/heads/{DEFAULT_BRANCH}"})
    legacy = ((data.get("repository") or {}).get("ref") or {}).get("branchProtectionRule") or {}
    if legacy.get("requiresStatusChecks"):
        contexts.update(legacy.get("requiredStatusCheckContexts") or [])
        strict = strict or legacy.get("requiresStrictStatusChecks") is True
    if not REQUIRED_CHECKS:
        return None, "REQUIRED_CHECKS must identify repository CI and security gates"
    missing = set(REQUIRED_CHECKS) - contexts
    if missing:
        return None, "branch protection does not enforce: " + ", ".join(sorted(missing))
    if not strict:
        return None, "branch protection must require checks against the latest target branch"
    for flag, method in (("allow_squash_merge", "SQUASH"), ("allow_merge_commit", "MERGE"), ("allow_rebase_merge", "REBASE")):
        if settings.get(flag):
            return method, "repository merge policy verified"
    return None, "repository has no supported merge method enabled"


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
            if any(same_repo(pr) and pr.get("head", {}).get("ref") == name for pr in closed):
                continue
            comparison = get(f"/repos/{REPO}/compare/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}...{urllib.parse.quote(sha, safe='')}")
            if int(comparison.get("ahead_by", 0)) > 0:
                create_or_reuse_pr((name, sha))
        if len(branches) < 100:
            return
    raise RuntimeError("Too many branches; refusing incomplete signal recovery")


def disable_native_auto_merge(pr: dict[str, Any]) -> None:
    if pr.get("auto_merge"):
        graphql("mutation($id:ID!){disablePullRequestAutoMerge(input:{pullRequestId:$id}){pullRequest{number}}}", {"id": pr["node_id"]})
        log(f"Disabled native auto-merge for held PR #{pr['number']}.")


def enable_native_auto_merge(pr: dict[str, Any]) -> None:
    method, reason = native_merge_policy()
    if method is None:
        disable_native_auto_merge(pr)
        log(f"PR #{pr['number']} auto-merge withheld: {reason}.")
        return
    if pr.get("auto_merge"):
        log(f"Native GitHub auto-merge is already enabled for PR #{pr['number']}.")
        return
    # gh queues native auto-merge when blocked, or completes the merge when
    # GitHub already allows it. Strict enforced checks protect the latest base;
    # --match-head-commit protects against a concurrent source push.
    flag = {"SQUASH": "--squash", "MERGE": "--merge", "REBASE": "--rebase"}[method]
    subprocess.run([
        "gh", "pr", "merge", str(pr["number"]), "--repo", REPO,
        "--auto", flag, "--match-head-commit", str(pr["head"]["sha"]),
    ], check=True)
    log(f"Requested guarded GitHub auto-merge for PR #{pr['number']} after exact-head checks passed.")


def reconcile_managed_prs() -> None:
    for listed in list_open_prs():
        # Observe holds/drafts even after native auto-merge was previously armed.
        if not (same_repo(listed) and listed.get("user", {}).get("login") == REPAIR_APP_LOGIN
                and MANAGED_LABEL.lower() in issue_labels(listed)):
            continue
        number = int(listed["number"])
        current = refresh_pr(number)
        if not managed_pr(current):
            disable_native_auto_merge(current)
            continue

        if current.get("mergeable") is not True:
            log(f"PR #{number} mergeability is not confirmed; deferring.")
            continue
        if current.get("mergeable_state") == "dirty" or current.get("mergeable") is False:
            log(f"PR #{number} has merge conflicts; automatic merge is withheld.")
            continue
        if update_branch_if_behind(current):
            continue

        green, reason = required_checks_green(current)
        if not green:
            log(f"PR #{number} not ready: {reason}.")
            continue

        expected_sha = str(current.get("head", {}).get("sha", ""))
        current = refresh_pr(number)
        if not managed_pr(current) or str(current.get("head", {}).get("sha", "")) != expected_sha:
            log(f"PR #{number} changed while being evaluated; deferring to the next reconciliation.")
            continue
        if update_branch_if_behind(current):
            continue

        base_branch = get(
            f"/repos/{REPO}/branches/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}"
        )
        base_sha = str(base_branch.get("commit", {}).get("sha", ""))
        pr_base_sha = str(current.get("base", {}).get("sha", ""))
        if base_sha != pr_base_sha:
            log(
                f"PR #{number} base moved from {pr_base_sha[:12]} to {base_sha[:12]}; "
                "waiting for GitHub to refresh mergeability."
            )
            continue

        enable_native_auto_merge(current)


def main() -> int:
    if not REQUIRED_CHECKS:
        raise RuntimeError("REQUIRED_CHECKS must list enforced CI/security gate names")
    if not REQUIRED_WORKFLOWS:
        raise RuntimeError(
            "REQUIRED_WORKFLOWS must list this repository's exact CI/security workflow names"
        )
    if not re.fullmatch(r"[A-Za-z0-9-]+\[bot\]", REPAIR_APP_LOGIN):
        raise RuntimeError("REPAIR_APP_LOGIN must identify the trusted repository GitHub App bot")

    ensure_label()
    create_or_reuse_pr()
    recover_branch_signals()
    reconcile_managed_prs()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"::error::{exc}", flush=True)
        raise

