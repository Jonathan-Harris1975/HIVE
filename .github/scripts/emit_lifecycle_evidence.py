#!/usr/bin/env python3
"""Emit a redacted lifecycle evidence record; unknown is never interpreted as pass."""
from __future__ import annotations
import argparse
import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
STATUSES = {"pass", "fail", "running", "unknown", "not_applicable"}
FINAL = {"GREEN", "RECOVERING", "REVIEW_REQUIRED", "SERVICE_BLOCKED", "UNKNOWN"}
FIELDS = [
    "repository", "pr_number", "head_sha", "merge_sha", "trigger_actor",
    "ci_gate", "autofix_used", "socket_status", "codeql_status",
    "gitleaks_status", "trivy_status", "codecov_status", "dast_status",
    "kilo_triggered", "kilo_trigger_reason", "repair_provider", "fallback_used",
    "fallback_reason", "repair_attempts", "mergify_admitted", "mergify_queued",
    "merged", "deployment_status", "deployed_sha", "deployment_verified",
    "human_hold", "failure_class", "final_state", "started_at", "completed_at",
]

def iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except ValueError:
        return None


class EvidenceUnavailable(Exception):
    """Metadata was absent, ambiguous, or outside the bounded read budget."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


def result_status(item):
    if not isinstance(item, dict):
        return "unknown"
    if item.get("status") != "completed":
        return "running" if item.get("status") in {"queued", "in_progress", "waiting", "pending"} else "unknown"
    conclusion = item.get("conclusion")
    return "pass" if conclusion == "success" else "fail" if conclusion in {"failure", "timed_out", "action_required", "startup_failure"} else "unknown"


def combine_statuses(values):
    if "fail" in values:
        return "fail"
    if "running" in values:
        return "running"
    return "pass" if values and all(value == "pass" for value in values) else "unknown"


def collect_current_evidence(record):
    """Read exact-current-head metadata; unavailable evidence stays unknown."""
    slug = os.environ.get("GITHUB_REPOSITORY", "")
    credential = os.environ.get("GH_TOKEN", "")
    if not credential or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", slug):
        return
    if slug.rsplit("/", 1)[-1] != record["repository"]:
        return
    deadline = time.monotonic() + 100
    opener = urllib.request.build_opener(NoRedirect())
    base = f"https://api.github.com/repos/{slug}"

    def read(path):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EvidenceUnavailable()
        request = urllib.request.Request(base + path, headers={
            "Authorization": f"Bearer {credential}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with opener.open(request, timeout=min(10, remaining)) as response:
                body = response.read(4_000_001)
            if len(body) > 4_000_000:
                raise EvidenceUnavailable()
            return json.loads(body)
        except (OSError, ValueError) as exc:
            raise EvidenceUnavailable() from exc

    def check(name, app):
        query = urllib.parse.urlencode({"check_name": name, "filter": "latest", "per_page": 100})
        payload = read(f"/commits/{record['head_sha']}/check-runs?{query}")
        rows = payload.get("check_runs")
        if not isinstance(rows, list) or len(rows) >= 100:
            raise EvidenceUnavailable()
        rows = [row for row in rows if row.get("head_sha") == record["head_sha"]
                and row.get("name") == name and row.get("app", {}).get("slug") == app]
        latest = max(rows, key=lambda row: int(row.get("id") or 0), default=None)
        return result_status(latest)

    def workflow(filename, branch, names, step=None):
        query = urllib.parse.urlencode({"head_sha": record["head_sha"], "branch": branch, "per_page": 100})
        payload = read(f"/actions/workflows/{filename}/runs?{query}")
        rows = payload.get("workflow_runs")
        if not isinstance(rows, list) or len(rows) >= 100:
            raise EvidenceUnavailable()
        rows = [row for row in rows if row.get("head_sha") == record["head_sha"]
                and row.get("head_branch") == branch
                and row.get("head_repository", {}).get("full_name") == slug
                and row.get("path") == f".github/workflows/{filename}"
                and row.get("event") != "pull_request"]
        latest = max(rows, key=lambda row: (int(row.get("run_number") or 0),
                     int(row.get("run_attempt") or 0), int(row.get("id") or 0)), default=None)
        status = result_status(latest)
        if status != "pass":
            return status
        payload = read(f"/actions/runs/{latest['id']}/jobs?filter=latest&per_page=100")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list) or len(jobs) >= 100:
            raise EvidenceUnavailable()
        statuses = []
        for name in names:
            matching = [job for job in jobs if job.get("name") == name and job.get("head_sha") == record["head_sha"]]
            job = max(matching, key=lambda row: int(row.get("id") or 0), default=None)
            statuses.append(result_status(job))
            if step and name == "OWASP ZAP full active scan":
                steps = job.get("steps", []) if job else []
                statuses.append(result_status(next((item for item in steps if item.get("name") in step), None)))
        return combine_statuses(statuses)

    fields = ("ci_gate", "socket_status", "codeql_status", "gitleaks_status",
              "trivy_status", "codecov_status", "dast_status", "deployment_status")
    try:
        repository = read("")
        branch = repository.get("default_branch")
        if repository.get("full_name") != slug or not isinstance(branch, str) or not branch:
            raise EvidenceUnavailable()
        head_path = "/commits/" + urllib.parse.quote(branch, safe="")
        if read(head_path).get("sha") != record["head_sha"]:
            record["failure_class"] = "stale_automation_state"
            return
        checks = {
            "ci_gate": ("ci-gate", "github-actions"),
            "codeql_status": ("CodeQL security alert gate", "github-actions"),
            "socket_status": ("Socket Security: Project Report", "socket-security"),
            "codecov_status": ("codecov/project", "codecov"),
        }
        for field, (name, app) in checks.items():
            try:
                record[field] = check(name, app)
            except EvidenceUnavailable:
                record[field] = "unknown"
        try:
            security_status = check("Trivy, Gitleaks and actionlint", "github-actions")
        except EvidenceUnavailable:
            security_status = "unknown"
        # A successful combined job proves its scanners passed. A failure
        # could be actionlint or either scanner, so do not invent attribution.
        record["trivy_status"] = "pass" if security_status == "pass" else "unknown"
        record["gitleaks_status"] = record["trivy_status"]
        deployment = (
            ("pages-deployment-watch.yml", ["watch"]) if record["repository"] == "IRS"
            else ("deployed-integration.yml", ["integration", "Deployed integration closure (exact SHA)"])
            if record["repository"] == "AIMS-UI"
            else ("deployed-integration.yml", ["integration"])
            if record["repository"] in {"HIVE-UI", "jonathan-harris-website"}
            else ("koyeb-deployment-watch.yml", ["watch"])
        )
        try:
            record["deployment_status"] = workflow(deployment[0], branch, deployment[1])
        except EvidenceUnavailable:
            record["deployment_status"] = "unknown"
        try:
            record["dast_status"] = workflow("dast.yml", branch,
                ["OWASP ZAP full active scan", "Persist evidence with R2 environment credentials"],
                step=("Run OWASP ZAP full scan", "Run authorised ZAP full scan"))
        except EvidenceUnavailable:
            record["dast_status"] = "unknown"
        # Recheck after the snapshot so a branch move cannot produce current green evidence.
        if read(head_path).get("sha") != record["head_sha"]:
            for field in fields:
                record[field] = "unknown"
            record["failure_class"] = "stale_automation_state"
            return
    except (EvidenceUnavailable, TypeError, KeyError, AttributeError):
        for field in fields:
            record[field] = "unknown"
        record["failure_class"] = "evidence_unavailable"
        return
    record["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record["deployed_sha"] = record["head_sha"] if record["deployment_status"] == "pass" else None
    record["deployment_verified"] = record["deployment_status"] == "pass"
    failed = [field for field in fields if record[field] == "fail"]
    if security_status == "fail":
        failed.append("Trivy, Gitleaks and actionlint")
    record["failed_check"] = ", ".join(failed) or None
    if failed:
        record["final_state"] = "SERVICE_BLOCKED"
        record["failure_class"] = "check_failed"
    elif all(record[field] == "pass" for field in fields):
        record["final_state"] = "GREEN"
        record["failure_class"] = None
    elif any(record[field] == "running" for field in fields):
        record["final_state"] = "RECOVERING"
        record["failure_class"] = "incomplete_evidence"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pr-number")
    parser.add_argument("--trigger-actor")
    parser.add_argument("--source-workflow")
    parser.add_argument("--source-conclusion")
    parser.add_argument("--started-at")
    parser.add_argument("--completed-at")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.head_sha):
        raise SystemExit("head SHA must be 40 hexadecimal characters")
    conclusion = args.source_conclusion
    allowed = {"success", "failure", "cancelled", "skipped", "timed_out", "action_required"}
    record = {field: None for field in FIELDS}
    record.update(
        repository=args.repository,
        repository_slug=os.environ.get("GITHUB_REPOSITORY") or None,
        pr_number=int(args.pr_number) if args.pr_number and args.pr_number.isdigit() else None,
        head_sha=args.head_sha.lower(),
        trigger_actor=args.trigger_actor,
        source_workflow=args.source_workflow,
        source_workflow_conclusion=conclusion if conclusion in allowed else "unknown",
        ci_gate="unknown",
        autofix_used=None,
        socket_status="unknown",
        codeql_status="unknown",
        gitleaks_status="unknown",
        trivy_status="unknown",
        codecov_status="unknown",
        dast_status="unknown",
        kilo_triggered=None,
        kilo_trigger_reason=None,
        repair_provider=None,
        fallback_used=None,
        fallback_reason=None,
        repair_attempts=0,
        mergify_admitted=None,
        mergify_queued=None,
        merge_authority="github_native_auto_merge",
        github_auto_merge_enabled=None,
        github_auto_merge_queued=None,
        mergify_not_applicable_reason=(
            "GitHub native auto-merge is the live merge authority; "
            "no Mergify state is applicable."
        ),
        merged=None,
        deployment_status="unknown",
        deployed_sha=None,
        deployment_verified=None,
        deployment_not_applicable_reason=None,
        not_applicable_reasons={},
        human_hold=None,
        failure_class="incomplete_evidence",
        failed_check=None,
        proposed_solution=None,
        repair_prompt=None,
        risk_impact=None,
        final_state="UNKNOWN",
        started_at=iso(args.started_at),
        completed_at=iso(args.completed_at),
    )
    collect_current_evidence(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"record": "written", "repository": args.repository,
                      "final_state": record["final_state"],
                      "complete": all(record[field] == "pass" for field in (
                          "ci_gate", "socket_status", "codeql_status", "gitleaks_status",
                          "trivy_status", "codecov_status", "dast_status", "deployment_status",
                      ))}))


if __name__ == "__main__":
    main()
