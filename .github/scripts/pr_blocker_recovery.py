#!/usr/bin/env python3
"""Reconcile current PR blockers using trusted metadata and bounded agent repairs."""

from __future__ import annotations
import json
import os
from pathlib import Path
import re
import pr_repair_router as router

BOT_REVIEWERS = {"kilo-code-bot"}
HOLD_LABELS = {
    "autonomy:obsolete",
    "autonomy:human-hold",
    "autonomy:superseded",
    "hold",
    "do-not-merge",
    "needs-manual-review",
}
RECEIPT = re.compile(r"<!-- pr-blocker-resolution:(\{[^\n]*\}) -->")


def login(value):
    return str(value or "").removesuffix("[bot]")


def review_threads(number):
    rows = []
    cursor = None
    owner, name = router.REPO.split("/", 1)
    for _ in range(10):
        result = router.api(
            "POST",
            "/graphql",
            {
                "query": """query($owner:String!,$name:String!,$number:Int!,$cursor:String) {
          repository(owner:$owner,name:$name) { pullRequest(number:$number) {
            reviewThreads(first:100,after:$cursor) { pageInfo { hasNextPage endCursor }
              nodes { id isResolved isOutdated path line comments(first:50) {
                pageInfo { hasNextPage } nodes { body author { login } } } } } } } }""",
                "variables": {"owner": owner, "name": name, "number": number, "cursor": cursor},
            },
        )
        if result.get("errors"):
            raise RuntimeError("Unable to read complete review-thread metadata")
        page = result["data"]["repository"]["pullRequest"]["reviewThreads"]
        for thread in page["nodes"]:
            if thread["comments"]["pageInfo"]["hasNextPage"]:
                raise RuntimeError("Review thread exceeds the evidence limit")
            rows.append(thread)
        if not page["pageInfo"]["hasNextPage"]:
            return rows
        cursor = page["pageInfo"]["endCursor"]
    raise RuntimeError("Review-thread pagination exceeds safe limit")


def required_checks_pass(pr):
    # GitHub supplies the effective native branch requirements. Never infer them
    # from workflow names or accept an empty requirement set as approval.
    rulesets = router.all_pages(f"/repos/{router.REPO}/rulesets")
    rules = []
    for summary in rulesets:
        if summary.get("enforcement") != "active" or summary.get("target") != "branch":
            continue
        detail = router.api("GET", f"/repos/{router.REPO}/rulesets/{summary['id']}")
        includes = detail.get("conditions", {}).get("ref_name", {}).get("include", [])
        if "~DEFAULT_BRANCH" in includes or f"refs/heads/{router.DEFAULT}" in includes:
            rules.extend(detail.get("rules", []))
    required = [
        item
        for rule in rules
        if rule.get("type") == "required_status_checks"
        for item in rule.get("parameters", {}).get("required_status_checks", [])
    ]
    if not required:
        return False
    sha = pr["head"]["sha"]
    checks = []
    for page in range(1, 11):
        part = router.api(
            "GET",
            f"/repos/{router.REPO}/commits/{sha}/check-runs?filter=latest&per_page=100&page={page}",
        )["check_runs"]
        checks.extend(part)
        if len(part) < 100:
            break
    else:
        raise RuntimeError("Check pagination exceeds safe limit")
    statuses = router.api("GET", f"/repos/{router.REPO}/commits/{sha}/status")["statuses"]
    for item in required:
        context, app = item["context"], item.get("integration_id")
        matches = [
            c
            for c in checks
            if c["name"] == context and (not app or c.get("app", {}).get("id") == app)
        ]
        if matches:
            if any(
                c.get("status") != "completed" or c.get("conclusion") != "success" for c in matches
            ):
                return False
        elif app or not any(s["context"] == context and s["state"] == "success" for s in statuses):
            return False
    return True


def verified_receipts(comments, sha, base_sha):
    trusted = {login(router.REPAIR_APP_LOGIN)} - {""}
    receipts = {}
    for comment in comments:
        if login(comment.get("user", {}).get("login")) not in trusted:
            continue
        for match in RECEIPT.finditer(comment.get("body", "")):
            try:
                data = json.loads(match.group(1))
            except ValueError:
                continue
            if data.get("sha") != sha or data.get("base_sha") != base_sha:
                continue
            for item in data.get("threads", []):
                if (
                    isinstance(item, dict)
                    and re.fullmatch(r"PRRT_[A-Za-z0-9_-]+", str(item.get("id", "")))
                    and isinstance(item.get("evidence"), str)
                    and len(item["evidence"].strip()) >= 40
                ):
                    receipts[item["id"]] = item["evidence"]
    return receipts


def has_unresolved_repair_handoff(comments, sha):
    prefix = f"<!-- kilo-auto-repair:{sha}:"
    trusted = {login("github-actions[bot]")} - {""}
    return any(
        login(comment.get("user", {}).get("login")) in trusted
        and prefix in (comment.get("body") or "")
        and "dispatch-state: rejected-http-" not in (comment.get("body") or "")
        for comment in comments
    )


def current_head_failure_findings(pr):
    """Return bounded failed check evidence for this exact PR head, across providers."""
    sha = pr.get("head", {}).get("sha", "")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Invalid PR head SHA for check recovery")
    findings = []
    seen = set()
    encoded_sha = router.urllib.parse.quote(sha, safe="")
    for page in range(1, 11):
        payload = router.api(
            "GET",
            f"/repos/{router.REPO}/commits/{encoded_sha}/check-runs?filter=latest&per_page=100&page={page}",
        )
        rows = payload.get("check_runs", [])
        for check in rows:
            if check.get("head_sha") != sha or check.get("status") != "completed" or check.get("conclusion") not in {"failure", "timed_out"}:
                continue
            app = str((check.get("app") or {}).get("slug") or "external-check")
            # Never feed Kilo's own review status back into its repair loop.
            if app.startswith("kilo"):
                continue
            name = str(check.get("name") or "unnamed check")[:160]
            key = (app, name)
            if key in seen:
                continue
            seen.add(key)
            findings.append(f"Failed check on exact PR head {sha}: {app}/{name} (conclusion={check['conclusion']}).")
            if len(findings) >= 12:
                return findings
        if len(rows) < 100:
            break
    else:
        raise RuntimeError("Check-run pagination exceeds safe limit")

    for page in range(1, 11):
        status_payload = router.api(
            "GET", f"/repos/{router.REPO}/commits/{encoded_sha}/status?per_page=100&page={page}"
        )
        statuses = status_payload.get("statuses", [])
        for status in statuses:
            if status.get("state") not in {"failure", "error"}:
                continue
            creator = login((status.get("creator") or {}).get("login"))
            trusted_kilo_logins = {
                login(value) for value in (
                    *router.KILO,
                    router.KILO_IMPLEMENTER,
                    router.REPAIR_APP_LOGIN,
                ) if value
            }
            if creator in trusted_kilo_logins:
                continue
            name = str(status.get("context") or "unnamed status")[:160]
            key = ("commit-status", name)
            if key not in seen:
                seen.add(key)
                findings.append(f"Failed commit status on exact PR head {sha}: {name} (state={status['state']}).")
            if len(findings) >= 12:
                return findings
        if len(statuses) < 100:
            break
    else:
        raise RuntimeError("Commit-status pagination exceeds safe limit")
    return findings


def should_scan_current_head_failures(recovery):
    """Scan check failures unless a stronger or active bot repair owns this PR."""
    state = recovery.get("state")
    return (
        state == "no-conflict-or-review-blocker"
        or (state == "review-recovery" and not recovery.get("request_status"))
    )


def route_current_head_failures(number):
    """Route failures found by the scheduled all-PR sweep, with exact-head revalidation."""
    pr = router.pr_details(number)
    if not pr:
        return {"pr": number, "state": "excluded"}
    if {x.get("name") for x in pr.get("labels", [])} & HOLD_LABELS:
        return {"pr": number, "state": "excluded"}
    sha = pr["head"]["sha"]
    if pr.get("mergeable") is not True or pr.get("mergeable_state") in {"dirty", "behind"}:
        return {"pr": number, "state": "blocker-changed-before-check-scan", "head": sha}
    findings = current_head_failure_findings(pr)
    if not findings:
        return {"pr": number, "state": "no-failed-current-head-checks", "head": sha}
    fresh = router.pr_details(number)
    if (not fresh or fresh.get("head", {}).get("sha") != sha or
            fresh.get("mergeable") is not True or fresh.get("mergeable_state") in {"dirty", "behind"} or
            {x.get("name") for x in fresh.get("labels", [])} & HOLD_LABELS):
        return {"pr": number, "state": "changed-before-check-dispatch", "head": sha}
    request = router.dispatch(pr, "check", findings)
    return {"pr": number, "state": "check-" + request, "head": sha}


def escalate_attempt_limit(number, kind, sha):
    """Post one visible PR escalation when an automated repair category is exhausted."""
    marker = f"<!-- kilo-auto-repair-escalation:{sha}:{kind} -->"
    comments = router.all_pages(f"/repos/{router.REPO}/issues/{number}/comments")
    if any(
        item.get("user", {}).get("login") == "github-actions[bot]"
        and marker in (item.get("body") or "")
        for item in comments
    ):
        return "already-escalated"
    router.api(
        "POST",
        f"/repos/{router.REPO}/issues/{number}/comments",
        {"body": (
            f"{marker}\n"
            f"Automatic repair paused for `{kind}` at head `{sha}` after the configured attempt limit. "
            "A maintainer needs to inspect the latest repair handoffs and failed checks, then either correct the blocker "
            "or make a new commit to start a fresh exact-head recovery cycle. No merge or deployment was performed."
        )},
    )
    return "escalated"


def recover(number):
    pr = router.pr_details(number)
    if not pr or {x.get("name") for x in pr.get("labels", [])} & HOLD_LABELS:
        return {"pr": number, "state": "excluded"}
    base = router.api(
        "GET", f"/repos/{router.REPO}/commits/{router.urllib.parse.quote(router.DEFAULT, safe='')}"
    )["sha"]
    sha = pr["head"]["sha"]
    comments = router.all_pages(f"/repos/{router.REPO}/issues/{number}/comments")
    if has_unresolved_repair_handoff(comments, sha):
        return {"pr": number, "state": "repair-handoff-pending", "head": sha}
    if pr.get("mergeable") is False or pr.get("mergeable_state") == "dirty":
        # Include base SHA in the deduplication identity: a later base change is
        # a new conflict, rather than a lifetime two-attempt limit for this PR.
        request = router.dispatch(
            pr,
            "merge-conflict-" + base,
            [f"Current PR #{number} has merge conflicts at head {sha} against base {base}."],
        )
        return {"pr": number, "state": "conflict-" + request, "head": sha, "base": base}
    if pr.get("mergeable") is not True:
        return {"pr": number, "state": "mergeability-pending"}
    if pr.get("mergeable_state") == "behind":
        request = router.dispatch(
            pr,
            "branch-behind-" + base,
            [f"PR #{number} is behind required current base {base}; head is {sha}."],
        )
        return {"pr": number, "state": "behind-" + request, "head": sha, "base": base}
    threads = [t for t in review_threads(number) if not t["isResolved"]]
    if not threads:
        return {"pr": number, "state": "no-conflict-or-review-blocker"}
    repair_reviewers = BOT_REVIEWERS | {login(router.KILO_IMPLEMENTER), login(router.REPAIR_APP_LOGIN)} - {""}
    bot_threads = [
        t
        for t in threads
        if t["comments"]["nodes"]
        and login(t["comments"]["nodes"][0].get("author", {}).get("login")) in repair_reviewers
    ]
    receipts = verified_receipts(comments, sha, base)
    resolved = []
    if receipts and required_checks_pass(pr):
        for thread in bot_threads:
            if thread["id"] not in receipts:
                continue
            # Re-read both tips before every mutation; stale evidence never
            # dismisses a review on a newer head or changed base.
            fresh = router.pr_details(number)
            tip = router.api(
                "GET",
                f"/repos/{router.REPO}/commits/{router.urllib.parse.quote(router.DEFAULT, safe='')}",
            )["sha"]
            if (
                not fresh
                or fresh["head"]["sha"] != sha
                or tip != base
                or fresh.get("mergeable") is not True
                or {x.get("name") for x in fresh.get("labels", [])} & HOLD_LABELS
            ):
                return {"pr": number, "state": "changed-during-recovery"}
            result = router.api(
                "POST",
                "/graphql",
                {
                    "query": "mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}",
                    "variables": {"id": thread["id"]},
                },
            )
            if result.get("errors") or not result.get("data", {}).get(
                "resolveReviewThread", {}
            ).get("thread", {}).get("isResolved"):
                raise RuntimeError("Review thread resolution was not confirmed")
            resolved.append(thread["id"])
    remaining = [t for t in bot_threads if t["id"] not in resolved and t["id"] not in receipts]
    request = None
    if remaining:
        evidence = [
            f"Thread {t['id']} at {t['path']}:{t.get('line') or 'historical line'} (outdated={t['isOutdated']}): "
            + router.review_evidence(t["comments"]["nodes"][0]["body"], t["path"])[:3500]
            for t in remaining
        ]
        request = router.dispatch(pr, "review-threads-" + base, evidence)
    return {
        "pr": number,
        "state": "review-recovery",
        "head": sha,
        "base": base,
        "resolved": resolved,
        "request_status": request,
        "bot_threads_remaining": len(remaining),
        "human_threads_remaining": len(threads) - len(bot_threads),
    }


def candidate_numbers(event):
    actor = event.get("review") or event.get("comment")
    if actor and (event.get("pull_request") or event.get("issue", {}).get("pull_request")):
        trusted = BOT_REVIEWERS | {login(router.KILO_IMPLEMENTER), login(router.REPAIR_APP_LOGIN)}
        if login(actor.get("user", {}).get("login")) not in trusted and actor.get(
            "author_association"
        ) not in {"OWNER", "MEMBER", "COLLABORATOR"}:
            return []
    if event.get("pull_request"):
        return [int(event["pull_request"]["number"])]
    if event.get("issue", {}).get("pull_request"):
        return [int(event["issue"]["number"])]
    requested = event.get("inputs", {}).get("pr_number")
    if requested:
        if not str(requested).isdigit():
            raise ValueError("Invalid requested PR number")
        return [int(requested)]
    if event.get("workflow_run", {}).get("event") == "pull_request":
        pr = router.resolve_run_pr(event["workflow_run"])
        return [int(pr["number"])] if pr else []
    return [int(pr["number"]) for pr in router.all_pages(f"/repos/{router.REPO}/pulls?state=open")]


def recovery_error_detail(exc):
    """Map safe router outcomes to actionable bounded recovery diagnostics."""
    if isinstance(exc, router.urllib.error.HTTPError):
        return {
            "error_code": "github-api-http",
            "http_status": exc.code,
            "guidance": "Check the recovery job's GitHub token permissions and repository access.",
        }
    reason = router.safe_route_error(exc)
    match = re.fullmatch(r"Kilo trigger returned HTTP ([0-9]{3})", reason)
    if match:
        status = int(match.group(1))
        if 400 <= status < 500:
            guidance = "Kilo rejected the handoff; no repair completed. Verify provider authorization before the bounded retry."
        else:
            guidance = "Provider acceptance is uncertain; inspect the pending handoff marker with Kilo before retrying."
        return {
            "error_code": "repair-agent-provider-error",
            "http_status": status,
            "guidance": guidance,
        }
    if reason == "Kilo trigger could not be reached":
        return {
            "error_code": "repair-agent-unavailable",
            "guidance": "Provider acceptance is uncertain; inspect the pending handoff marker with Kilo before retrying.",
        }
    if reason == "Kilo webhook configuration is missing, unsupported or invalid":
        return {
            "error_code": "repair-agent-configuration",
            "guidance": "Check the stored Kilo webhook configuration without printing its value.",
        }
    return {
        "error_code": "recovery-error",
        "guidance": "Inspect trusted recovery metadata and permissions; raw exception details are withheld.",
    }


def main():
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    results = []
    errors = 0
    for number in candidate_numbers(event):
        try:
            recovery = recover(number)
            if should_scan_current_head_failures(recovery):
                try:
                    recovery["check_recovery"] = route_current_head_failures(number)
                except Exception as scan_exc:
                    errors += 1
                    recovery["check_recovery"] = {
                        "state": "check-scan-error",
                        "error_type": type(scan_exc).__name__,
                    }
                    print(f"::error::Current-head check scan for PR #{number} failed ({type(scan_exc).__name__}); inspect the recovery job.")
            attempt_state = str(recovery.get("state", ""))
            attempt_kind = None
            if attempt_state.endswith("-attempt-limit"):
                attempt_kind = attempt_state[:-len("-attempt-limit")]
            elif recovery.get("request_status") == "attempt-limit":
                attempt_kind = "review"
            elif recovery.get("check_recovery", {}).get("state") == "check-attempt-limit":
                attempt_kind = "check"
            if attempt_kind:
                try:
                    recovery["escalation"] = escalate_attempt_limit(
                        number, attempt_kind, recovery.get("head", "")
                    )
                except Exception as escalation_exc:
                    errors += 1
                    recovery["escalation"] = "attempt-limit-escalation-error"
                    print(f"::error::Attempt-limit escalation for PR #{number} failed ({type(escalation_exc).__name__}); inspect the recovery job.")
            results.append(recovery)
        except Exception as exc:
            errors += 1
            detail = recovery_error_detail(exc)
            results.append(
                {
                    "pr": number,
                    "state": "recovery-error",
                    "error_type": type(exc).__name__,
                    **detail,
                }
            )
            print(
                f"::error::Blocker recovery for PR #{number}: {detail['error_code']}"
                + (f" (HTTP {detail['http_status']})" if "http_status" in detail else "")
                + ". "
                + detail["guidance"]
            )
    text = json.dumps({"repository": router.REPO, "results": results}, indent=2) + "\n"
    Path("pr-blocker-recovery.json").write_text(text)
    summary = (
        "# Automatic PR blocker recovery\n\n"
        + "\n".join(
            f"- PR #{r['pr']}: {r['state']}"
            + (
                f" — {r['error_code']}"
                + (f" (HTTP {r['http_status']})" if "http_status" in r else "")
                + ". "
                + r["guidance"]
                if "error_code" in r
                else ""
            )
            for r in results
        )
        + "\n"
    )
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
        stream.write(summary)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
