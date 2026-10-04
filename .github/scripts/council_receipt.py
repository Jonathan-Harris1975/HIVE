"""Validate an authenticated Council completion against live GitHub evidence.

This emits an exact-SHA receipt, not a freeze implementation. A dispatch HTTP
response, model prose, skipped DAST or an old successful run cannot certify it.
"""

import json
import os
import re
import urllib.request
from pathlib import Path


class EvidenceError(RuntimeError):
    pass


class GitHub:
    def __init__(self, repo, token):
        self.root = "https://api.github.com/repos/" + repo
        self.token = token

    def request(self, path, data=None):
        request = urllib.request.Request(
            self.root + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer " + self.token,
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def pages(self, path, key=None):
        results = []
        for page in range(1, 11):
            separator = "&" if "?" in path else "?"
            payload = self.request(f"{path}{separator}per_page=100&page={page}")
            items = payload[key] if key else payload
            results.extend(items)
            if len(items) < 100:
                return results
        raise EvidenceError(
            "Evidence exceeds bounded pagination; human review required"
        )


def require(condition, reason):
    if not condition:
        raise EvidenceError(reason)


def verify(
    api,
    policy,
    target,
    council_id,
    completion_id,
    kilo_login,
    disposition,
    dast_enabled,
):
    require(
        re.fullmatch(r"[0-9a-f]{40}", target) is not None, "Invalid exact target SHA"
    )
    require(
        disposition == "READY_FOR_COUNCIL_ACCEPTANCE",
        "Council did not return acceptance",
    )
    default = api.request("")["default_branch"]
    require(
        api.request(f"/commits/{default}")["sha"] == target, "Council target is stale"
    )
    completion = api.request(f"/actions/runs/{completion_id}")
    require(
        completion["workflow_id"]
        == api.request("/actions/workflows/council-completion.yml")["id"],
        "Wrong completion workflow",
    )
    require(
        completion["event"] == "workflow_dispatch"
        and completion["head_branch"] == default
        and completion["head_sha"] == target,
        "Completion must run on the exact default-branch commit",
    )
    for key in ("actor", "triggering_actor"):
        actor = completion.get(key) or {}
        require(
            actor.get("login") == kilo_login and actor.get("type") == "Bot",
            "Only the configured Kilo App may complete Council",
        )
    council = api.request(f"/actions/runs/{council_id}")
    require(
        council["workflow_id"] == api.request("/actions/workflows/council.yml")["id"],
        "Wrong source Council workflow",
    )
    require(
        council["event"] == "workflow_dispatch"
        and council["head_branch"] == default
        and council["head_sha"] == target,
        "Council run is not bound to the current target",
    )
    require(
        council["created_at"] <= completion["created_at"], "Completion predates Council"
    )
    require(
        council["status"] == "in_progress"
        or (council["status"] == "completed" and council["conclusion"] == "success"),
        "Source Council run failed or was cancelled",
    )
    jobs = api.pages(f"/actions/runs/{council_id}/jobs", "jobs")
    require(
        any(
            step.get("name") == policy["dispatch_step"]
            and step.get("conclusion") == "success"
            for job in jobs
            for step in job.get("steps", [])
        ),
        "Council was not successfully dispatched",
    )
    runs = api.pages(f"/actions/runs?head_sha={target}", "workflow_runs")
    proofs = []
    required = [policy["ci"], "codeql.yml", "security.yml", policy["deployment"]]
    if dast_enabled and policy["dast"]:
        required.append("dast.yml")
    for filename in required:
        workflow_id = api.request("/actions/workflows/" + filename)["id"]
        candidates = [
            run
            for run in runs
            if run["workflow_id"] == workflow_id
            and run["head_sha"] == target
            and run["head_branch"] == default
            and run["event"] in {"push", "workflow_dispatch", "workflow_run"}
        ]
        require(bool(candidates), "Missing exact-SHA evidence: " + filename)
        latest = max(
            candidates, key=lambda run: (int(run["id"]), int(run.get("run_attempt", 1)))
        )
        require(
            latest["status"] == "completed" and latest["conclusion"] == "success",
            "Latest exact-SHA run is not green: " + filename,
        )
        if filename == "dast.yml":
            jobs = api.pages(f"/actions/runs/{latest['id']}/jobs", "jobs")
            require(
                any(job.get("conclusion") == "success" for job in jobs),
                "Enabled DAST was skipped",
            )
        proof = {
            "workflow": filename,
            "run_id": latest["id"],
            "attempt": latest.get("run_attempt", 1),
            "url": latest["html_url"],
        }
        if filename == policy["deployment"]:
            artifacts = api.pages(
                f"/actions/runs/{latest['id']}/artifacts", "artifacts"
            )
            expected = policy["artifact"] + target
            matching = [
                item
                for item in artifacts
                if item["name"] == expected
                and not item["expired"]
                and re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest") or "")
            ]
            require(
                len(matching) == 1, "Missing retained exact-SHA deployment evidence"
            )
            proof["artifact"] = {
                key: matching[0][key] for key in ("id", "name", "digest")
            }
        proofs.append(proof)
    for pr in api.pages("/pulls?state=open"):
        labels = {label["name"] for label in pr.get("labels", [])}
        require(
            "autonomy:human-hold" not in labels
            and not (
                "autonomy:repair" in labels
                and not labels.intersection(
                    {"autonomy:obsolete", "autonomy:superseded"}
                )
            ),
            "Unresolved autonomous repair or human hold",
        )
    require(
        api.request(f"/commits/{default}")["sha"] == target,
        "Target changed during evidence verification",
    )
    return {
        "schema": 1,
        "repository": os.environ.get("GITHUB_REPOSITORY", ""),
        "target_sha": target,
        "council_run_id": council_id,
        "completion_run_id": completion_id,
        "authenticated_actor": kilo_login,
        "disposition": disposition,
        "proofs": proofs,
        "dast": "verified"
        if dast_enabled and policy["dast"]
        else "N/A: existing opt-in disabled or MAST has no authorised target",
    }


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    api = GitHub(repo, os.environ["GH_TOKEN"])
    policy = json.loads(Path(".council/receipt-policy.json").read_text())
    target = os.environ["TARGET_SHA"]
    receipt = verify(
        api,
        policy,
        target,
        int(os.environ["COUNCIL_RUN_ID"]),
        int(os.environ["GITHUB_RUN_ID"]),
        os.environ["KILO_COUNCIL_LOGIN"],
        os.environ["DISPOSITION"],
        os.environ.get("DAST_ENABLED") == "true",
    )
    if os.environ.get("RECEIPT_MODE") == "publish":
        artifacts = api.pages(
            f"/actions/runs/{receipt['completion_run_id']}/artifacts", "artifacts"
        )
        expected = f"council-receipt-{target}-{receipt['council_run_id']}"
        require(
            any(
                item["name"] == expected
                and not item["expired"]
                and re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("digest") or "")
                for item in artifacts
            ),
            "Completion receipt was not retained",
        )
        require(
            api.request("/commits/" + api.request("")["default_branch"])["sha"]
            == target,
            "Target changed before receipt publication",
        )
        api.request(
            "/statuses/" + target,
            {
                "state": "success",
                "context": "Repository Council acceptance",
                "description": f"Council {receipt['council_run_id']}; verified receipt {receipt['completion_run_id']}",
                "target_url": f"https://github.com/{repo}/actions/runs/{receipt['completion_run_id']}",
            },
        )
    else:
        Path("council-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
