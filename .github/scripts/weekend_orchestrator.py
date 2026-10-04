#!/usr/bin/env python3
"""Weekend orchestrator: settle Mergify, verify exact-SHA evidence, then release Council.

Sequence (Europe/London): the Renovate window closes at 02:30; this script starts at
02:30, waits for every automation PR to be merged or closed (Mergify completing), runs
CI, CodeQL and Security on the final default-branch SHA, confirms the production
deployment for that SHA, waits until at least one hour after the window closed, and only
then dispatches the Repository Council. Failures are left to the existing Kilo repair
path; the orchestrator waits for the repair to merge, re-verifies and retries (bounded).

Exit codes: 0 Council dispatched (or dry run complete), 3 HUMAN_HOLD, 1 unexpected error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
UTC = timezone.utc

WINDOW_CLOSE = (2, 30)          # Renovate window closes Sunday 02:30 London
COUNCIL_DELAY = timedelta(hours=1)
GUARD_GRACE = timedelta(minutes=59)  # tolerate delayed cron starts

REQUIRED_WORKFLOWS = {
    "CI": "ci.yml",
    "CodeQL": "codeql.yml",
    "Security and repository quality": "security.yml",
}
DEPLOY_WORKFLOW_NAME = "Koyeb production deployment watch"
DEPLOY_WORKFLOW_FILE = "koyeb-deployment-watch.yml"
COUNCIL_FILE = "council.yml"
TRUSTED_FILE = "trusted-automation.yml"

AUTOMATION_LABELS = {"autonomy:admitted", "autonomy:repair", "autonomy:human-hold",
                     "autonomy:kilo-implementation"}
MERGIFY_QUEUE_PREFIX = "mergify/merge-queue/"
RENOVATE_LOGIN = "renovate[bot]"


def log(message: str) -> None:
    print(f"[{datetime.now(LONDON):%H:%M:%S}] {message}", flush=True)


# --------------------------------------------------------------------------- time

def window_close_for(now: datetime) -> datetime:
    """Return the Sunday 02:30 London instant belonging to the week of ``now``."""
    local = now.astimezone(LONDON)
    days_back = (local.weekday() + 1) % 7  # Monday=0 ... Sunday=6 -> Sunday gives 0
    sunday = (local - timedelta(days=days_back)).date()
    return datetime(sunday.year, sunday.month, sunday.day, *WINDOW_CLOSE, tzinfo=LONDON)


def council_not_before(now: datetime) -> datetime:
    return window_close_for(now) + COUNCIL_DELAY


def in_launch_window(now: datetime) -> bool:
    """True from the window close until 59 minutes later, on the Sunday only."""
    local = now.astimezone(LONDON)
    if local.weekday() != 6:
        return False
    close = window_close_for(local)
    return close <= local <= close + GUARD_GRACE


# ------------------------------------------------------------------------ GitHub

@dataclass
class Config:
    repo: str
    token: str
    branch: str = "main"
    settle_minutes: int = 180
    nudge_minutes: int = 15
    idle_refresh_minutes: int = 20
    max_repairs: int = 2
    poll_seconds: int = 60
    dry_run: bool = False
    ci_timeout: int = 75
    codeql_timeout: int = 45
    security_timeout: int = 45
    deploy_timeout: int = 30


class Api:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def call(self, method: str, path: str, payload: Any | None = None,
             expected: tuple[int, ...] = (200, 201, 202, 204)) -> Any:
        url = path if path.startswith("https://") else "https://api.github.com" + path
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.cfg.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read()
                if response.status not in expected:
                    raise RuntimeError(f"{method} {path} returned {response.status}")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise RuntimeError(f"{method} {path} failed: HTTP {exc.code} {detail}") from exc

    def get(self, path: str) -> Any:
        return self.call("GET", path)

    def pages(self, path: str, key: str | None = None) -> list[dict]:
        out: list[dict] = []
        page = 1
        sep = "&" if "?" in path else "?"
        while True:
            payload = self.get(f"{path}{sep}per_page=100&page={page}")
            items = payload.get(key, []) if key else payload
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1


class Orchestrator:
    def __init__(self, cfg: Config, api: Api | None = None, clock=time.monotonic,
                 sleep=time.sleep, now=lambda: datetime.now(UTC)) -> None:
        self.cfg = cfg
        self.api = api or Api(cfg)
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self.stages: list[dict[str, Any]] = []
        self.refreshed: set[int] = set()
        self.last_nudge = 0.0

    # ------------------------------------------------------------------ records
    def record(self, stage: str, outcome: str, detail: str = "") -> None:
        self.stages.append({"stage": stage, "outcome": outcome, "detail": detail,
                            "at": self.now().isoformat()})
        log(f"{stage}: {outcome} {detail}".strip())

    # ----------------------------------------------------------------- queries
    def head_sha(self) -> str:
        return self.api.get(f"/repos/{self.cfg.repo}/commits/{self.cfg.branch}")["sha"]

    def open_prs(self) -> list[dict]:
        return self.api.pages(f"/repos/{self.cfg.repo}/pulls?state=open")

    @staticmethod
    def labels(pr: dict) -> set[str]:
        return {x["name"] for x in pr.get("labels", [])}

    def automation_prs(self, prs: list[dict]) -> list[dict]:
        """Open PRs the autonomous system is responsible for finishing."""
        found = []
        for pr in prs:
            head = pr.get("head", {}).get("ref", "")
            login = pr.get("user", {}).get("login", "")
            body = pr.get("body") or ""
            renovate_auto = (login == RENOVATE_LOGIN and "dependency:auto-eligible" in self.labels(pr)
                             and "dependency:manual" not in self.labels(pr))
            if (self.labels(pr) & AUTOMATION_LABELS or head.startswith(MERGIFY_QUEUE_PREFIX)
                    or renovate_auto):
                found.append(pr)
        return found

    def inflight_pr_runs(self) -> int:
        total = 0
        for status in ("queued", "in_progress"):
            payload = self.api.get(
                f"/repos/{self.cfg.repo}/actions/runs?status={status}&event=pull_request&per_page=100")
            total += int(payload.get("total_count", 0))
        return total

    def pending_minimum_age(self) -> str:
        """Count Renovate updates waiting on minimumReleaseAge, from the dashboard issue."""
        try:
            issues = self.api.get(f"/repos/{self.cfg.repo}/issues?state=open&per_page=100")
        except RuntimeError:
            return "unknown"
        for issue in issues:
            if ("pull_request" not in issue
                    and issue.get("user", {}).get("login") == RENOVATE_LOGIN
                    and "dependency dashboard" in str(issue.get("title", "")).lower()):
                return str(count_pending_section(issue.get("body") or ""))
        return "unknown"

    # ------------------------------------------------------------------ stages
    def nudge(self, prs: list[dict]) -> None:
        now = self.clock()
        if now - self.last_nudge >= self.cfg.nudge_minutes * 60:
            self.last_nudge = now
            try:
                self.api.call("POST",
                              f"/repos/{self.cfg.repo}/actions/workflows/{TRUSTED_FILE}/dispatches",
                              {"ref": self.cfg.branch})
                log("Nudged trusted-automation reconciliation.")
            except RuntimeError as exc:
                log(f"Could not nudge trusted automation: {exc}")
        for pr in prs:
            number = int(pr["number"])
            if "autonomy:admitted" not in self.labels(pr) or number in self.refreshed:
                continue
            updated = datetime.fromisoformat(pr["updated_at"].replace("Z", "+00:00"))
            if self.now() - updated >= timedelta(minutes=self.cfg.idle_refresh_minutes):
                self.refreshed.add(number)
                try:
                    self.api.call("POST", f"/repos/{self.cfg.repo}/issues/{number}/comments",
                                  {"body": "@mergifyio refresh"})
                    log(f"Asked Mergify to refresh idle admitted PR #{number}.")
                except RuntimeError as exc:
                    log(f"Could not refresh PR #{number}: {exc}")

    def settle(self) -> bool:
        """Wait until Mergify has completed: no automation PR left, nothing running."""
        deadline = self.clock() + self.cfg.settle_minutes * 60
        while True:
            prs = self.automation_prs(self.open_prs())
            running = self.inflight_pr_runs()
            if not prs and running == 0:
                self.record("settle", "complete", "no automation PRs open and no PR runs in flight")
                return True
            if self.clock() >= deadline:
                numbers = ", ".join(f"#{p['number']}" for p in prs) or "none"
                self.record("settle", "timeout",
                            f"open automation PRs: {numbers}; PR runs in flight: {running}")
                return False
            log(f"Waiting for Mergify: {len(prs)} automation PR(s) open, {running} PR run(s) in flight.")
            self.nudge(prs)
            self.sleep(self.cfg.poll_seconds)

    def dispatch(self, workflow_file: str, inputs: dict[str, str] | None = None) -> datetime:
        started = self.now()
        payload: dict[str, Any] = {"ref": self.cfg.branch}
        if inputs:
            payload["inputs"] = inputs
        self.api.call("POST", f"/repos/{self.cfg.repo}/actions/workflows/{workflow_file}/dispatches",
                      payload)
        return started

    def find_run(self, workflow_file: str, sha: str, since: datetime, events: tuple[str, ...]) -> dict | None:
        payload = self.api.get(
            f"/repos/{self.cfg.repo}/actions/workflows/{workflow_file}/runs"
            f"?head_sha={sha}&branch={self.cfg.branch}&per_page=30")
        candidates = []
        for run in payload.get("workflow_runs", []):
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if run.get("event") in events and created >= since - timedelta(seconds=30):
                candidates.append(run)
        return max(candidates, key=lambda r: int(r["id"])) if candidates else None

    def wait_run(self, name: str, workflow_file: str, sha: str, since: datetime,
                 events: tuple[str, ...], timeout_minutes: int) -> str:
        deadline = self.clock() + timeout_minutes * 60
        while self.clock() < deadline:
            run = self.find_run(workflow_file, sha, since, events)
            if run is not None and run.get("status") == "completed":
                return str(run.get("conclusion"))
            self.sleep(self.cfg.poll_seconds)
        return "timeout"

    def verify(self, sha: str) -> list[str]:
        """Run the evidence workflows on ``sha``; return the failing stage names."""
        failures: list[str] = []
        timeouts = {"CI": self.cfg.ci_timeout, "CodeQL": self.cfg.codeql_timeout,
                    "Security and repository quality": self.cfg.security_timeout}
        started = {name: self.dispatch(file) for name, file in REQUIRED_WORKFLOWS.items()}
        for name, file in REQUIRED_WORKFLOWS.items():
            result = self.wait_run(name, file, sha, started[name], ("workflow_dispatch",), timeouts[name])
            self.record(f"verify:{name}", result)
            if result != "success":
                failures.append(name)
        if failures:
            return failures

        # The Koyeb watcher normally starts from CI's workflow_run completion; if it does
        # not appear, dispatch it so the exact-SHA deployment proof is never skipped.
        since = started["CI"]
        result = self.wait_run(DEPLOY_WORKFLOW_NAME, DEPLOY_WORKFLOW_FILE, sha, since,
                               ("workflow_run", "workflow_dispatch"), 10)
        if result == "timeout":
            self.record("verify:deployment", "dispatching", "watcher did not start from CI completion")
            since = self.dispatch(DEPLOY_WORKFLOW_FILE)
            result = self.wait_run(DEPLOY_WORKFLOW_NAME, DEPLOY_WORKFLOW_FILE, sha, since,
                                   ("workflow_dispatch",), self.cfg.deploy_timeout)
        else:
            run = self.find_run(DEPLOY_WORKFLOW_FILE, sha, since, ("workflow_run", "workflow_dispatch"))
            if run is not None and run.get("status") != "completed":
                result = self.wait_run(DEPLOY_WORKFLOW_NAME, DEPLOY_WORKFLOW_FILE, sha, since,
                                       ("workflow_run", "workflow_dispatch"), self.cfg.deploy_timeout)
        self.record("verify:deployment", result)
        if result != "success":
            failures.append(DEPLOY_WORKFLOW_NAME)
        return failures

    def wait_for_repair_carrier(self) -> None:
        """Give the existing failure path time to open its carrier PR before re-settling."""
        deadline = self.clock() + 10 * 60
        while self.clock() < deadline:
            if any("autonomy:repair" in self.labels(p) for p in self.open_prs()):
                return
            self.sleep(self.cfg.poll_seconds)

    def hold_for_council_time(self) -> None:
        not_before = council_not_before(self.now())
        while self.now().astimezone(LONDON) < not_before:
            log(f"Waiting until {not_before:%H:%M} London before releasing Council.")
            self.sleep(min(self.cfg.poll_seconds, 300))

    def release_council(self, sha: str, pending: str) -> bool:
        self.hold_for_council_time()
        if self.head_sha() != sha:
            self.record("council", "deferred", "default branch moved before release")
            return False
        blockers = [p for p in self.open_prs()
                    if self.labels(p) & {"autonomy:human-hold"}
                    or ("autonomy:repair" in self.labels(p)
                        and not self.labels(p) & {"autonomy:obsolete", "autonomy:superseded"})]
        if blockers:
            self.record("council", "blocked", "unresolved repair/human-hold PRs: "
                        + ", ".join(f"#{p['number']}" for p in blockers))
            return False
        if self.cfg.dry_run:
            self.record("council", "dry-run", f"would dispatch for {sha[:12]}")
            return True
        started = self.dispatch(COUNCIL_FILE, {"target_sha": sha, "pending_minimum_age": pending})
        self.record("council", "dispatched", f"sha {sha[:12]}; pending minimum-age updates: {pending}")
        result = self.wait_run("Repository Council", COUNCIL_FILE, sha, started, ("workflow_dispatch",), 30)
        self.record("council:run", result)
        return result == "success"

    # --------------------------------------------------------------------- main
    def run(self) -> int:
        pending = self.pending_minimum_age()
        repairs = 0
        while True:
            if not self.settle():
                return self.hold("Mergify did not complete within the settle limit")
            sha = self.head_sha()
            failures = self.verify(sha)
            if self.head_sha() != sha and not failures:
                self.record("verify", "restarted", "default branch moved during verification")
                continue
            if not failures:
                break
            if repairs >= self.cfg.max_repairs:
                return self.hold("verification still failing after repair attempts: " + ", ".join(failures))
            repairs += 1
            self.record("repair", f"attempt {repairs}", "waiting for the Kilo repair path: " + ", ".join(failures))
            self.wait_for_repair_carrier()
        ok = self.release_council(sha, pending)
        return 0 if ok else self.hold("Council was not released or did not complete")

    def hold(self, reason: str) -> int:
        self.record("HUMAN_HOLD", "hold", reason)
        return 3

    def report(self) -> dict[str, Any]:
        return {"repository": self.cfg.repo, "branch": self.cfg.branch, "stages": self.stages}


def count_pending_section(body: str) -> int:
    """Count list items under the dashboard's 'Pending Status Checks' heading."""
    count = 0
    inside = False
    for line in body.splitlines():
        if line.startswith("#"):
            inside = "pending status checks" in line.lower()
            continue
        if inside and re.match(r"\s*[-*]\s*\[[ xX]\]", line):
            count += 1
    return count


def write_summary(orch: Orchestrator, exit_code: int) -> None:
    lines = ["## Weekend orchestration", "", "| Stage | Outcome | Detail |", "| --- | --- | --- |"]
    for item in orch.stages:
        lines.append(f"| {item['stage']} | {item['outcome']} | {item['detail']} |")
    lines.append("")
    lines.append(f"Exit code: {exit_code} (0 complete, 3 HUMAN_HOLD)")
    text = "\n".join(lines) + "\n"
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
    out = os.environ.get("ORCHESTRATION_REPORT_PATH", "weekend-orchestration.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(orch.report(), handle, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--guard", action="store_true", help="only decide whether this start is inside the launch window")
    parser.add_argument("--skip-guard", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.guard:
        run = args.skip_guard or in_launch_window(datetime.now(UTC))
        output = os.environ.get("GITHUB_OUTPUT")
        line = f"run={'true' if run else 'false'}\n"
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(line)
        log(f"Launch window check: {'inside' if run else 'outside'} (London now {datetime.now(LONDON):%a %H:%M}).")
        return 0

    cfg = Config(repo=os.environ["GITHUB_REPOSITORY"], token=os.environ["GH_TOKEN"],
                 branch=os.environ.get("DEFAULT_BRANCH", "main"),
                 settle_minutes=int(os.environ.get("SETTLE_MINUTES", "180")),
                 dry_run=args.dry_run)
    orch = Orchestrator(cfg)
    try:
        code = orch.run()
    except Exception as exc:  # noqa: BLE001 - report and fail visibly
        orch.record("error", "exception", f"{type(exc).__name__}: {exc}")
        code = 1
    write_summary(orch, code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
