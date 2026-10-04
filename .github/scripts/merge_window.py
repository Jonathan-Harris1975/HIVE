"""Protected default-branch Council freeze; never execute pull-request code.

The CI gate records a trusted immutable run marker and invalidates PR statuses
before final CI PASS. Markers survive repair merges because they belong to CI
runs, rather than just the current default-branch commit. Only a completed,
authenticated receipt publisher can release the marker.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from council_receipt import EvidenceError, GitHub, accepted_receipt, require

__all__ = ["EvidenceError"]

LONDON = ZoneInfo("Europe/London")
CONTEXT = "Council merge window"
AUTHORIZATION = "Council repair authorization"
HOLD = {
    "do-not-merge",
    "needs-manual-review",
    "autonomy:human-hold",
    "autonomy:obsolete",
    "autonomy:superseded",
}


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def window(created, start):
    now = instant(created).astimezone(LONDON)
    day, clock = start.split()
    weekday = {"Fri": 4, "Sat": 5}[day]
    hour, minute = map(int, clock.split(":"))
    date = now.date() - timedelta(days=(now.weekday() - weekday) % 7)
    slot = datetime(date.year, date.month, date.day, hour, minute, tzinfo=LONDON)
    if not slot <= now < slot + timedelta(minutes=150):
        return None
    friday = slot.date() - timedelta(days=(slot.weekday() - 4) % 7)
    return (
        datetime(friday.year, friday.month, friday.day, 20, tzinfo=LONDON)
        .astimezone(timezone.utc)
        .isoformat()
    )


def latest_status(api, sha, context):
    matching = [
        s for s in api.pages(f"/commits/{sha}/statuses") if s["context"] == context
    ]
    return max(matching, key=lambda s: int(s["id"])) if matching else None


def trusted(status):
    return bool(
        status
        and status.get("state") == "success"
        and status.get("creator", {}).get("login") == "github-actions[bot]"
        and status.get("creator", {}).get("type") == "Bot"
    )


def source_run(api, policy, run, default):
    return (
        run.get("workflow_id")
        == api.request("/actions/workflows/" + policy["ci"])["id"]
        and run.get("head_branch") == default
        and run.get("event") in {"push", "workflow_dispatch"}
        and window(run["created_at"], policy["ci_start"]) is not None
    )


def freeze_name(run):
    return f"council-freeze-{run['id']}-{run.get('run_attempt', 1)}-{run['head_sha']}"


def kept_artifact(api, run_id, name):
    artifacts = api.pages(f"/actions/runs/{run_id}/artifacts", "artifacts")
    kept = [
        a
        for a in artifacts
        if a["name"] == name
        and not a["expired"]
        and re.fullmatch(r"sha256:[0-9a-f]{64}", a.get("digest") or "")
    ]
    return len(kept) == 1


def evidence_at(run):
    return run.get("run_started_at") or run["created_at"]


def anchor(api, policy, repo, default):
    runs = api.pages("/actions/workflows/" + policy["ci"] + "/runs", "workflow_runs")
    for run in sorted(runs, key=lambda r: (evidence_at(r), int(r["id"])), reverse=True):
        if not source_run(api, policy, run, default):
            continue
        if kept_artifact(api, run["id"], freeze_name(run)):
            return run
        # A completed successful canonical CI inside its slot must have retained
        # its freeze marker. Deletion, expiry and pre-installation runs require
        # review; an attacker cannot conceal a freeze by overwriting a status.
        require(
            not (
                run.get("status") == "completed" and run.get("conclusion") == "success"
            ),
            "Successful scheduled CI has no retained freeze evidence",
        )
    return None


def released(api, policy, repo, run, default, kilo, dast):
    current = api.request(f"/commits/{default}")["sha"]
    completions = api.pages(
        "/actions/workflows/council-completion.yml/runs", "workflow_runs"
    )
    for completion in sorted(completions, key=lambda r: int(r["id"]), reverse=True):
        if completion["created_at"] < evidence_at(run):
            continue
        if not (
            completion.get("status") == "completed"
            and completion.get("conclusion") == "success"
            and completion.get("event") == "workflow_dispatch"
            and completion.get("head_branch") == default
            and all(
                (completion.get(k) or {}).get("login") == kilo
                and (completion.get(k) or {}).get("type") == "Bot"
                for k in ("actor", "triggering_actor")
            )
        ):
            continue
        sha = completion["head_sha"]
        status = latest_status(api, sha, "Repository Council acceptance")
        if not trusted(status):
            continue
        match = re.fullmatch(
            r"Council (\d+); verified receipt (\d+)", status.get("description") or ""
        )
        if not match or int(match[2]) != completion["id"]:
            continue
        if (
            status.get("target_url")
            != f"https://github.com/{repo}/actions/runs/{completion['id']}"
        ):
            continue
        council = api.request(f"/actions/runs/{match[1]}")
        if not (
            council["workflow_id"]
            == api.request("/actions/workflows/council.yml")["id"]
            and council["head_sha"] == sha
            and council["head_branch"] == default
            and council["event"] == "workflow_dispatch"
            and council["status"] == "completed"
            and council["conclusion"] == "success"
            and evidence_at(run) <= council["created_at"] <= completion["created_at"]
        ):
            continue
        artifacts = api.pages(
            f"/actions/runs/{completion['id']}/artifacts", "artifacts"
        )
        kept = [
            a
            for a in artifacts
            if a["name"] == f"council-receipt-{sha}-{match[1]}"
            and not a["expired"]
            and re.fullmatch(r"sha256:[0-9a-f]{64}", a.get("digest") or "")
        ]
        if len(kept) != 1:
            continue
        if sha == current:
            accepted_receipt(api, policy, repo, kilo, dast, evidence_at(run))
        else:
            # After certification ordinary merges may advance main. Preserve the
            # completed cycle, but never accept a receipt from divergent history.
            compare = api.request(f"/compare/{sha}...{current}")
            if compare.get("behind_by") != 0 or compare.get("status") not in {
                "ahead",
                "identical",
            }:
                continue
        return True
    return False


def failure_current(api, policy, failed, base, envelope, default):
    run = api.request(f"/actions/runs/{failed}")
    workflows = {policy["ci"], policy["deployment"], "security.yml", "codeql.yml"}
    if policy["dast"]:
        workflows.add("dast.yml")
    ids = {api.request("/actions/workflows/" + name)["id"] for name in workflows}
    require(
        run["workflow_id"] in ids
        and run["head_sha"] == base
        and run["head_branch"] == default
        and run["event"] in {"push", "workflow_dispatch", "workflow_run"}
        and instant(run["created_at"]) >= instant(envelope)
        and run["status"] == "completed"
        and run["conclusion"] == "failure",
        "Repair failure is not current-envelope evidence",
    )
    newer = [
        r
        for r in api.pages(f"/actions/runs?head_sha={base}", "workflow_runs")
        if r["workflow_id"] == run["workflow_id"]
        and r["head_branch"] == default
        and r["event"] in {"push", "workflow_dispatch", "workflow_run"}
    ]
    require(bool(newer), "No live failure evidence")
    latest = max(newer, key=lambda r: (int(r["id"]), int(r.get("run_attempt", 1))))
    require(
        latest["id"] == run["id"]
        and latest.get("run_attempt", 1) == run.get("run_attempt", 1)
        and latest["status"] == "completed"
        and latest["conclusion"] == "failure",
        "Repair is superseded by a newer run or attempt",
    )


def repair_allowed(api, policy, repo, pr, run, base, default, kilo):
    if not (
        pr.get("user", {}).get("login") == kilo
        and pr.get("user", {}).get("type") == "Bot"
        and pr["head"]["repo"]["full_name"] == repo
        and "autonomy:kilo-implementation" in {x["name"] for x in pr.get("labels", [])}
    ):
        return False
    status = latest_status(api, pr["head"]["sha"], AUTHORIZATION)
    match = re.fullmatch(
        r"Repair (\d+); CI (\d+)/(\d+); base ([0-9a-f]{40})",
        (status or {}).get("description") or "",
    )
    if (
        not trusted(status)
        or not match
        or int(match[2]) != run["id"]
        or int(match[3]) != int(run.get("run_attempt", 1))
        or match[4] != base
    ):
        return False
    producer_match = re.fullmatch(
        r"https://github\.com/" + re.escape(repo) + r"/actions/runs/(\d+)",
        status.get("target_url") or "",
    )
    if not producer_match:
        return False
    producer = api.request(f"/actions/runs/{producer_match[1]}")
    if not (
        producer["workflow_id"]
        == api.request("/actions/workflows/trusted-automation.yml")["id"]
        and producer["head_branch"] == default
        and producer["head_sha"] == base
        and producer["event"]
        in {"workflow_run", "schedule", "workflow_dispatch", "issue_comment"}
        and producer["status"] == "completed"
        and producer["conclusion"] == "success"
    ):
        return False
    name = f"council-repair-{pr['head']['sha']}-{match[1]}-{run['id']}-{run.get('run_attempt', 1)}-{base}"
    if not kept_artifact(api, producer["id"], name):
        return False
    failure_current(
        api,
        policy,
        int(match[1]),
        base,
        window(run["created_at"], policy["ci_start"]),
        default,
    )
    return True


def authorize_repair(api, policy, repo, pr, carrier, app, run, base, default, kilo):
    """Called only after existing trusted admission verifies identity/files/CI."""
    require(
        pr["user"]["login"] == kilo and pr["user"]["type"] == "Bot",
        "Wrong repair implementer",
    )
    require(
        carrier["user"]["login"] == app
        and carrier["user"]["type"] == "Bot"
        and carrier["head"]["repo"]["full_name"] == repo
        and carrier["state"] == "open"
        and carrier["base"]["ref"] == default
        and re.fullmatch(r"autonomy/repair-\d+", carrier["head"]["ref"]) is not None
        and not HOLD.intersection({x["name"] for x in carrier.get("labels", [])}),
        "Untrusted repair carrier",
    )
    body = carrier.get("body") or ""
    require(f"Failed commit: `{base}`" in body, "Repair carrier base is stale")
    matches = re.findall(
        r"Failed run:\s*https://github\.com/"
        + re.escape(repo)
        + r"/actions/runs/(\d+)(?![\w/-])",
        body,
    )
    require(len(matches) == 1, "Repair carrier must identify one failed run")
    failed = int(matches[0])
    failure_current(
        api,
        policy,
        failed,
        base,
        window(run["created_at"], policy["ci_start"]),
        default,
    )
    if repair_allowed(api, policy, repo, pr, run, base, default, kilo):
        return
    producer = int(os.environ["CURRENT_RUN_ID"])
    name = f"council-repair-{pr['head']['sha']}-{failed}-{run['id']}-{run.get('run_attempt', 1)}-{base}"
    folder = Path(".council-repair-authorizations")
    folder.mkdir(exist_ok=True)
    require(
        not any(p.name != name + ".json" for p in folder.glob("*.json")),
        "Another repair receipt is being retained in this run; retry next reconciliation",
    )
    (folder / (name + ".json")).write_text(
        json.dumps(
            {
                "pr": pr["number"],
                "head": pr["head"]["sha"],
                "base": base,
                "failed_run": failed,
                "freeze_run": run["id"],
                "carrier": carrier["number"],
            }
        )
    )
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("repair_artifact=" + name + "\n")
    api.request(
        f"/statuses/{pr['head']['sha']}",
        {
            "state": "success",
            "context": AUTHORIZATION,
            "description": f"Repair {failed}; CI {run['id']}/{run.get('run_attempt', 1)}; base {base}",
            "target_url": f"https://github.com/{repo}/actions/runs/{producer}",
        },
    )


def publish_pr(api, policy, repo, pr, run, base, default, kilo, closed):
    number = pr["number"]
    pr = api.request(f"/pulls/{number}")
    require(
        api.request(f"/commits/{default}")["sha"] == base,
        "Default head changed during freeze reconciliation",
    )
    if pr["state"] != "open" or pr["base"]["ref"] != default:
        return
    blocked = pr.get("draft") or HOLD.intersection(
        {x["name"] for x in pr.get("labels", [])}
    )
    allowed = not blocked and (
        run is None
        or closed
        or repair_allowed(api, policy, repo, pr, run, base, default, kilo)
    )
    api.request(
        f"/statuses/{pr['head']['sha']}",
        {
            "state": "success" if allowed else "failure",
            "context": CONTEXT,
            "description": "Council window open"
            if allowed
            else "Council freeze or human hold; merge blocked",
            "target_url": f"https://github.com/{repo}/actions/runs/{run['id']}"
            if run
            else pr["html_url"],
        },
    )


def reconcile(api, policy, repo, kilo, dast, recorded_run=None):
    default = api.request("")["default_branch"]
    base = api.request(f"/commits/{default}")["sha"]
    prs = api.pages("/pulls?state=open")
    # Revoke previous successes before any evidence read can fail. New PR heads
    # have no success in this required context until their own reconciliation.
    for pr in prs:
        if pr["base"]["ref"] == default:
            api.request(
                f"/statuses/{pr['head']['sha']}",
                {
                    "state": "pending",
                    "context": CONTEXT,
                    "description": "Revalidating Council freeze and exact PR head",
                },
            )
    run = recorded_run or anchor(api, policy, repo, default)
    closed = bool(
        not recorded_run
        and run
        and released(api, policy, repo, run, default, kilo, dast)
    )
    for pr in prs:
        publish_pr(api, policy, repo, pr, run, base, default, kilo, closed)
    require(
        api.request(f"/commits/{default}")["sha"] == base,
        "Default head changed during freeze reconciliation",
    )


def mark_pass(api, policy, repo, run_id, kilo, dast):
    default = api.request("")["default_branch"]
    run = api.request(f"/actions/runs/{run_id}")
    if window(run["created_at"], policy["ci_start"]) is None:
        print("Outside repository CI slot; no new Council evidence marker")
        return
    require(
        source_run(api, policy, run, default),
        "Cannot record PR or noncanonical CI evidence",
    )
    require(
        api.request(f"/commits/{default}")["sha"] == run["head_sha"],
        "CI target changed before final PASS",
    )
    api.request(
        f"/statuses/{run['head_sha']}",
        {
            "state": "success",
            "context": f"Council evidence freeze {run_id}",
            "description": "Envelope " + window(run["created_at"], policy["ci_start"]),
            "target_url": f"https://github.com/{repo}/actions/runs/{run_id}",
        },
    )
    reconcile(api, policy, repo, kilo, dast, recorded_run=run)
    Path("council-freeze.json").write_text(
        json.dumps(
            {
                "run": run_id,
                "attempt": run.get("run_attempt", 1),
                "sha": run["head_sha"],
                "envelope": window(run["created_at"], policy["ci_start"]),
            }
        )
    )
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("artifact=" + freeze_name(run) + "\n")


def main():
    api = GitHub(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    policy = json.loads(Path(".council/receipt-policy.json").read_text())
    kilo = os.environ.get("KILO_REPAIR_PR_LOGIN") or "kilo-code-bot[bot]"
    dast = os.environ.get("DAST_ENABLED", "").lower() == "true"
    if os.environ.get("FREEZE_MODE") == "record":
        mark_pass(
            api,
            policy,
            os.environ["GITHUB_REPOSITORY"],
            int(os.environ["GITHUB_RUN_ID"]),
            kilo,
            dast,
        )
    else:
        reconcile(api, policy, os.environ["GITHUB_REPOSITORY"], kilo, dast)


if __name__ == "__main__":
    main()
