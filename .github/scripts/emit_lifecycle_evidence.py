#!/usr/bin/env python3
"""Emit a redacted lifecycle evidence record; unknown is never interpreted as pass."""
from __future__ import annotations
import argparse
import json
import os
import re
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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"record": "written", "repository": args.repository,
                      "final_state": "UNKNOWN", "complete": False}))


if __name__ == "__main__":
    main()
