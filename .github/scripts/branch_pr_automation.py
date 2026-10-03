#!/usr/bin/env python3
"""Create and safely auto-merge pull requests for approved development branches.

Runs only from the trusted default-branch controller and never checks out or
executes code from the pushed branch.
"""
from __future__ import annotations
import json, os, re, urllib.error, urllib.parse, urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"
TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ.get("REPO") or os.environ["GITHUB_REPOSITORY"]
DEFAULT_BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
REPAIR_APP_LOGIN = os.environ.get("REPAIR_APP_LOGIN", "")
REQUIRED_WORKFLOWS = [x.strip() for x in os.environ.get("REQUIRED_WORKFLOWS", "").split("|") if x.strip()]
MANAGED_LABEL = "automation:branch-pr"
ALLOWED_PREFIXES = ("fix/", "feat/", "chore/", "ci/", "work/", "codex/")
EXCLUDED_PREFIXES = ("autonomy/", "renovate/", "dependabot/", "mergify/", "tmp/", "temp/", "internal/")
BLOCKING_LABELS = {"autonomy:human-hold", "do-not-merge", "do not merge", "hold"}
BRANCH_RE = re.compile(r"^(fix|feat|chore|ci|work|codex)/[A-Za-z0-9._/-]+$")


def log(message: str) -> None:
    print(message, flush=True)


@dataclass
class ApiError(RuntimeError):
    status: int
    body: str


def request(method: str, path: str, data: Any | None = None, expected: tuple[int, ...] = (200,)) -> Any:
    url = path if path.startswith("http") else API + path
    payload = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, method=method)
    for key, value in {"Accept":"application/vnd.github+json", "Authorization":f"Bearer {TOKEN}", "X-GitHub-Api-Version":"2022-11-28"}.items(): req.add_header(key, value)
    if payload is not None: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read()
            if response.status not in expected: raise ApiError(response.status, raw.decode("utf-8", "replace"))
            return None if not raw else json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode("utf-8", "replace")) from exc


def get(path: str) -> Any: return request("GET", path)
def post(path: str, data: Any | None = None, expected: tuple[int, ...] = (200, 201)) -> Any: return request("POST", path, data, expected)
def put(path: str, data: Any | None = None, expected: tuple[int, ...] = (200, 202)) -> Any: return request("PUT", path, data, expected)


def graphql(query: str, variables: dict[str, Any]) -> Any:
    result = request("POST", GRAPHQL, {"query": query, "variables": variables}, (200,))
    if result.get("errors"): raise RuntimeError("GitHub GraphQL error: " + json.dumps(result["errors"])[:1000])
    return result.get("data", {})


def payload() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path: return {}
    with open(path, encoding="utf-8") as handle: value = json.load(handle)
    return value if isinstance(value, dict) else {}


def labels(pr: dict[str, Any]) -> set[str]: return {str(x.get("name", "")).strip().lower() for x in pr.get("labels", [])}
def same_repo(pr: dict[str, Any]) -> bool: return str(pr.get("head", {}).get("repo", {}).get("full_name", "")) == REPO


def allowed(branch: str) -> bool:
    return (branch != DEFAULT_BRANCH and any(branch.startswith(x) for x in ALLOWED_PREFIXES)
            and not any(branch.startswith(x) for x in EXCLUDED_PREFIXES)
            and ".." not in branch and "//" not in branch and not branch.endswith("/")
            and BRANCH_RE.fullmatch(branch) is not None)


def ensure_label() -> None:
    try: post(f"/repos/{REPO}/labels", {"name":MANAGED_LABEL,"color":"1D76DB","description":"PR created and managed by trusted branch automation"}, (201,))
    except ApiError as exc:
        if exc.status != 422: raise


def open_prs() -> list[dict[str, Any]]:
    out = []
    for page in range(1, 11):
        chunk = get(f"/repos/{REPO}/pulls?state=open&per_page=100&page={page}"); out.extend(chunk)
        if len(chunk) < 100: return out
    raise RuntimeError("More than 1,000 open PRs; refusing incomplete reconciliation")


def exact_pr(items: list[dict[str, Any]], branch: str) -> dict[str, Any] | None:
    found = [p for p in items if same_repo(p) and p.get("head", {}).get("ref") == branch and p.get("base", {}).get("ref") == DEFAULT_BRANCH]
    if len(found) > 1: raise RuntimeError(f"Multiple open PRs for {branch} -> {DEFAULT_BRANCH}")
    return found[0] if found else None


def signal_candidate() -> tuple[str, str] | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_run": return None
    event = payload(); run = event.get("workflow_run") or {}
    if event.get("action") != "completed" or run.get("name") != "Branch PR signal" or run.get("event") != "push" or run.get("conclusion") != "success": return None
    branch, sha = str(run.get("head_branch") or ""), str(run.get("head_sha") or "")
    if str((run.get("head_repository") or {}).get("full_name", "")) != REPO or not allowed(branch) or not re.fullmatch(r"[0-9a-f]{40}", sha): return None
    current = get(f"/repos/{REPO}/branches/{urllib.parse.quote(branch, safe='')}")
    if str(current.get("commit", {}).get("sha", "")) != sha: return None
    comparison = get(f"/repos/{REPO}/compare/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}...{urllib.parse.quote(branch, safe='')}")
    return (branch, sha) if int(comparison.get("ahead_by", 0)) > 0 else None


def create_or_reuse() -> None:
    candidate = signal_candidate()
    if candidate is None: return
    branch, sha = candidate; existing = exact_pr(open_prs(), branch)
    if existing: log(f"Using existing PR #{existing['number']} for {branch} -> {DEFAULT_BRANCH}."); return
    commit = get(f"/repos/{REPO}/commits/{sha}"); message = str(commit.get("commit", {}).get("message") or "").strip(); first, _, rest = message.partition("\n")
    title = (first.strip() or branch.replace("/", ": ", 1))[:240]
    body = ("Created automatically by the repository's trusted branch automation.\n\n"
            f"- Source branch: `{branch}`\n- Target branch: `{DEFAULT_BRANCH}`\n- Signalled head: `{sha}`\n\n"
            "Normal pull-request CI and security workflows must succeed before native GitHub auto-merge is requested."
            + (f"\n\n### Commit details\n\n{rest.strip()[:4000]}" if rest.strip() else ""))
    try: created = post(f"/repos/{REPO}/pulls", {"title":title,"head":branch,"base":DEFAULT_BRANCH,"body":body,"maintainer_can_modify":True}, (201,))
    except ApiError as exc:
        if exc.status != 422: raise
        existing = exact_pr(open_prs(), branch)
        if not existing: raise
        log(f"Creation raced with another actor; using PR #{existing['number']}."); return
    number = int(created["number"]); post(f"/repos/{REPO}/issues/{number}/labels", {"labels":[MANAGED_LABEL]}, (200,)); log(f"Created managed PR #{number} for {branch}.")


def latest_runs(sha: str) -> dict[str, dict[str, Any]]:
    query = urllib.parse.urlencode({"head_sha":sha,"event":"pull_request","per_page":100}); data = get(f"/repos/{REPO}/actions/runs?{query}"); out = {}
    for run in data.get("workflow_runs", []):
        name = str(run.get("name", "")); old = out.get(name)
        if old is None or int(run.get("id", 0)) > int(old.get("id", 0)): out[name] = run
    return out


def green(pr: dict[str, Any]) -> tuple[bool, str]:
    sha = str(pr.get("head", {}).get("sha", "")); runs = latest_runs(sha)
    for name in REQUIRED_WORKFLOWS:
        run = runs.get(name)
        if run is None: return False, f"{name!r} has not run on {sha[:12]}"
        if run.get("status") != "completed" or run.get("conclusion") != "success": return False, f"{name!r} is {run.get('status')}/{run.get('conclusion')}"
    return True, "required workflows succeeded"


def managed(pr: dict[str, Any]) -> bool:
    return (pr.get("state") == "open" and not pr.get("draft") and same_repo(pr) and pr.get("base", {}).get("ref") == DEFAULT_BRANCH
            and allowed(str(pr.get("head", {}).get("ref", ""))) and pr.get("user", {}).get("login") == REPAIR_APP_LOGIN
            and MANAGED_LABEL in labels(pr) and not labels(pr).intersection(BLOCKING_LABELS))


def update_if_behind(pr: dict[str, Any]) -> bool:
    if pr.get("mergeable_state") != "behind": return False
    try: put(f"/repos/{REPO}/pulls/{pr['number']}/update-branch", {"expected_head_sha":pr.get("head", {}).get("sha")}, (202,)); log(f"Updated PR #{pr['number']} from {DEFAULT_BRANCH}; waiting for fresh CI.")
    except ApiError as exc:
        if exc.status not in (403, 409, 422): raise
        log(f"PR #{pr['number']} is behind but GitHub could not update it automatically ({exc.status}).")
    return True


def enable_auto_merge(pr: dict[str, Any]) -> None:
    if pr.get("auto_merge"): return
    mutation = "mutation($id:ID!){enablePullRequestAutoMerge(input:{pullRequestId:$id,mergeMethod:SQUASH}){pullRequest{number state autoMergeRequest{enabledAt}}}}"
    graphql(mutation, {"id":pr["node_id"]}); log(f"Requested native GitHub auto-merge for PR #{pr['number']} after exact-head checks passed.")


def reconcile() -> None:
    for listed in open_prs():
        if not managed(listed): continue
        current = get(f"/repos/{REPO}/pulls/{listed['number']}")
        if not managed(current): continue
        if current.get("mergeable_state") == "dirty" or current.get("mergeable") is False: log(f"PR #{current['number']} has merge conflicts; withholding auto-merge."); continue
        if update_if_behind(current): continue
        ok, reason = green(current)
        if not ok: log(f"PR #{current['number']} not ready: {reason}."); continue
        sha = str(current.get("head", {}).get("sha", "")); current = get(f"/repos/{REPO}/pulls/{current['number']}")
        if not managed(current) or str(current.get("head", {}).get("sha", "")) != sha: continue
        if update_if_behind(current): continue
        base = get(f"/repos/{REPO}/branches/{urllib.parse.quote(DEFAULT_BRANCH, safe='')}")
        if str(base.get("commit", {}).get("sha", "")) != str(current.get("base", {}).get("sha", "")): continue
        enable_auto_merge(current)


def main() -> int:
    if not REQUIRED_WORKFLOWS: raise RuntimeError("REQUIRED_WORKFLOWS is required")
    if not re.fullmatch(r"[A-Za-z0-9-]+\[bot\]", REPAIR_APP_LOGIN): raise RuntimeError("REPAIR_APP_LOGIN must identify the trusted GitHub App bot")
    ensure_label(); create_or_reuse(); reconcile(); return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"::error::{exc}", flush=True); raise
