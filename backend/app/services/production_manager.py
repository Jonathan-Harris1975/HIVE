from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import Settings
from app.core.governed_repositories import DEFAULT_GITHUB_SOURCES, GOVERNED_REPOSITORY_IDS
from app.services.repo_health import build_repo_health_report

PRODUCTION_MANAGER = "HIVE"
READINESS_AUTHORITY = "deterministic_ci_security_deployment_runtime_gates"
REMEDIATION_EXECUTOR = "Kilo"
TECHNICAL_ESCALATION = "CTO"
HUMAN_AUTHORITY = "owner"

OWNER_ONLY_TRIGGERS: tuple[str, ...] = (
    "secrets_or_credentials",
    "irreversible_production_action",
    "security_policy_exception",
    "legal_or_commercial_decision",
    "destructive_data_operation",
)

REQUIRED_WORKFLOWS: dict[str, tuple[str, ...]] = {
    "HIVE": ("CI", "Security and repository quality", "CodeQL", "Item 6 hardening"),
    "HIVE-UI": ("HIVE-UI CI", "Security and repository quality", "CodeQL", "Item 6 hardening"),
    "AIMS": ("AIMS CI", "Security and repository quality", "CodeQL", "Item 6 hardening"),
    "AIMS-UI": ("Validate AIMS UI", "Security and repository quality", "CodeQL", "Item 6 hardening"),
    "RAMS": ("CI", "Security and repository quality", "CodeQL", "Item 6 hardening"),
    "MAST": ("MAST CI", "Security and repository quality", "CodeQL"),
    "IRS": ("Production verification", "Security and repository quality", "CodeQL", "Item 6 hardening"),
    "Website": ("Production readiness", "Security and repository quality", "CodeQL", "Item 6 hardening"),
}

GITHUB_API_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
GITHUB_CLIENT_LIMITS = httpx.Limits(max_connections=16, max_keepalive_connections=8)
GITHUB_RUNS_PER_PAGE = 100
GITHUB_RUNS_MAX_PAGES = 10
GITHUB_RETRY_ATTEMPTS = 3
GITHUB_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

DEPLOYMENT_WORKFLOWS: dict[str, str] = {
    "HIVE": "Koyeb production deployment watch",
    "HIVE-UI": "HIVE-UI deployed integration",
    "AIMS": "Koyeb production deployment watch",
    "AIMS-UI": "AIMS-UI deployed integration",
    "RAMS": "Koyeb production deployment watch",
    "MAST": "Koyeb production deployment watch",
    "IRS": "IRS Cloudflare Pages deployment watch",
    "Website": "Website deployed integration",
}

_GREEN_HEALTH = {"healthy", "standby", "maintenance"}
_DEGRADED_HEALTH = {"degraded", "starting", "not_configured", "partial", "missing", "unknown"}
_BLOCKED_HEALTH = {
    "down",
    "blocked",
    "unavailable",
    "failed",
    "failure",
    "error",
    "not_ready",
    "forbidden",
    "unauthorized",
}


def _production_state(status: object) -> str:
    value = str(status or "").strip().lower()
    if value in _GREEN_HEALTH:
        return "GREEN"
    if value in _BLOCKED_HEALTH:
        return "BLOCKED"
    if value in _DEGRADED_HEALTH:
        return "DEGRADED"
    return "DEGRADED"


def _combine_states(*states: str) -> str:
    if "BLOCKED" in states:
        return "BLOCKED"
    if "DEGRADED" in states:
        return "DEGRADED"
    return "GREEN"


def _repo_contract(repo_id: str) -> dict[str, Any]:
    return {
        "repository_id": repo_id,
        "github_repository": DEFAULT_GITHUB_SOURCES[repo_id],
        "ecosystem_manager": PRODUCTION_MANAGER,
        "readiness_authority": READINESS_AUTHORITY,
        "remediation_executor": REMEDIATION_EXECUTOR,
        "technical_escalation": TECHNICAL_ESCALATION,
        "human_authority": HUMAN_AUTHORITY,
        "required_workflows": list(REQUIRED_WORKFLOWS[repo_id]),
        "deployment_workflow": DEPLOYMENT_WORKFLOWS[repo_id],
        "autonomous_actions": [
            "observe",
            "validate",
            "gate",
            "report",
            "bounded_repair_pr",
            "deployment_verification",
        ],
        "owner_only_triggers": list(OWNER_ONLY_TRIGGERS),
    }


def production_governance_contract() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "role": "ecosystem_production_manager",
        "manager": PRODUCTION_MANAGER,
        "separation_of_duties": {
            "readiness_authority": READINESS_AUTHORITY,
            "remediation_executor": REMEDIATION_EXECUTOR,
            "technical_escalation": TECHNICAL_ESCALATION,
            "human_authority": HUMAN_AUTHORITY,
            "rule": "The remediation executor must not certify its own repair. Repository gates and live verification remain authoritative.",
        },
        "owner_only_triggers": list(OWNER_ONLY_TRIGGERS),
        "repositories": [_repo_contract(repo_id) for repo_id in GOVERNED_REPOSITORY_IDS],
    }


def _run_state(run: dict[str, Any] | None, *, allow_skipped: bool = False) -> tuple[str, str]:
    if not run:
        return "DEGRADED", "missing"
    status = str(run.get("status") or "").lower()
    conclusion = str(run.get("conclusion") or "").lower()
    if status != "completed":
        return "DEGRADED", "pending"
    if conclusion == "success" or (allow_skipped and conclusion == "skipped"):
        return "GREEN", conclusion
    if conclusion in {"failure", "timed_out", "startup_failure", "action_required"}:
        return "BLOCKED", conclusion
    return "DEGRADED", conclusion or "unknown"


async def _github_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, object] | None = None,
) -> httpx.Response:
    """Read GitHub with bounded retries for transient provider failures."""

    for attempt in range(1, GITHUB_RETRY_ATTEMPTS + 1):
        try:
            response = await client.get(url, params=params)
        except httpx.TransportError:
            if attempt >= GITHUB_RETRY_ATTEMPTS:
                raise
            await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
            continue

        rate_limited = response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or bool(response.headers.get("retry-after"))
        )
        if response.status_code not in GITHUB_RETRYABLE_STATUSES and not rate_limited:
            return response
        if attempt >= GITHUB_RETRY_ATTEMPTS:
            return response

        delay = 0.5 * (2 ** (attempt - 1))
        retry_after = response.headers.get("retry-after", "").strip()
        if retry_after.isdigit():
            delay = min(max(float(retry_after), 0.5), 5.0)
        await asyncio.sleep(delay)

    raise RuntimeError("GitHub retry loop exited unexpectedly")


async def _github_repo_evidence(
    client: httpx.AsyncClient,
    *,
    repo_id: str,
    branch: str,
) -> dict[str, Any]:
    source = DEFAULT_GITHUB_SOURCES[repo_id]
    encoded_repo = quote(source, safe="/")
    commit_response = await _github_get(
        client,
        f"https://api.github.com/repos/{encoded_repo}/commits/{quote(branch, safe='')}",
    )
    if commit_response.status_code != 200:
        return {
            "configured": True,
            "state": "DEGRADED",
            "reason": f"GitHub main-branch lookup returned HTTP {commit_response.status_code}.",
            "sha": None,
            "required_workflows": {},
            "deployment": {"workflow": DEPLOYMENT_WORKFLOWS[repo_id], "status": "unavailable"},
        }
    raw_commit = commit_response.json()
    sha = str(raw_commit.get("sha") or "").strip()
    if len(sha) != 40:
        return {
            "configured": True,
            "state": "DEGRADED",
            "reason": "GitHub main-branch lookup did not return a full commit SHA.",
            "sha": None,
            "required_workflows": {},
            "deployment": {"workflow": DEPLOYMENT_WORKFLOWS[repo_id], "status": "unavailable"},
        }

    required_names = {*REQUIRED_WORKFLOWS[repo_id], DEPLOYMENT_WORKFLOWS[repo_id]}
    runs: list[dict[str, Any]] = []
    exhausted = False
    for page in range(1, GITHUB_RUNS_MAX_PAGES + 1):
        runs_response = await _github_get(
            client,
            f"https://api.github.com/repos/{encoded_repo}/actions/runs",
            params={"branch": branch, "per_page": GITHUB_RUNS_PER_PAGE, "page": page},
        )
        if runs_response.status_code != 200:
            return {
                "configured": True,
                "state": "DEGRADED",
                "reason": f"GitHub workflow evidence returned HTTP {runs_response.status_code}.",
                "sha": sha,
                "required_workflows": {},
                "deployment": {"workflow": DEPLOYMENT_WORKFLOWS[repo_id], "status": "unavailable"},
            }

        raw_runs = runs_response.json()
        chunk = raw_runs.get("workflow_runs") if isinstance(raw_runs, dict) else None
        if not isinstance(chunk, list):
            chunk = []
        runs.extend(run for run in chunk if isinstance(run, dict))

        matched_names = {
            str(run.get("name") or "")
            for run in runs
            if str(run.get("head_sha") or "") == sha
        }
        if required_names.issubset(matched_names):
            exhausted = True
            break
        if len(chunk) < GITHUB_RUNS_PER_PAGE:
            exhausted = True
            break

    if not exhausted:
        return {
            "configured": True,
            "state": "DEGRADED",
            "reason": "GitHub workflow evidence exceeded the safe 1,000-run pagination window.",
            "sha": sha,
            "required_workflows": {},
            "deployment": {"workflow": DEPLOYMENT_WORKFLOWS[repo_id], "status": "unavailable"},
        }

    def latest(name: str) -> dict[str, Any] | None:
        return next(
            (
                run
                for run in runs
                if str(run.get("name") or "") == name
                and str(run.get("head_sha") or "") == sha
            ),
            None,
        )

    workflow_evidence: dict[str, Any] = {}
    gate_states: list[str] = []
    for name in REQUIRED_WORKFLOWS[repo_id]:
        run = latest(name)
        state, status = _run_state(run)
        gate_states.append(state)
        workflow_evidence[name] = {
            "state": state,
            "status": status,
            "run_id": run.get("id") if run else None,
        }

    deployment_name = DEPLOYMENT_WORKFLOWS[repo_id]
    deployment_run = latest(deployment_name)
    deployment_state, deployment_status = _run_state(deployment_run, allow_skipped=True)
    gate_states.append(deployment_state)

    state = _combine_states(*gate_states) if gate_states else "DEGRADED"
    return {
        "configured": True,
        "state": state,
        "reason": (
            "Required exact-SHA GitHub and deployment evidence is green."
            if state == "GREEN"
            else "Required exact-SHA GitHub or deployment evidence is incomplete or failed."
        ),
        "sha": sha,
        "required_workflows": workflow_evidence,
        "deployment": {
            "workflow": deployment_name,
            "state": deployment_state,
            "status": deployment_status,
            "run_id": deployment_run.get("id") if deployment_run else None,
        },
    }

async def _collect_all_gate_evidence(
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> dict[str, dict[str, Any]]:
    token = settings.github_token.strip()
    if not token:
        return {
            repo_id: {
                "configured": False,
                "state": "DEGRADED",
                "reason": "GITHUB_TOKEN is not configured; HIVE cannot certify repository gate evidence.",
                "sha": None,
                "required_workflows": {},
                "deployment": {"workflow": DEPLOYMENT_WORKFLOWS[repo_id], "status": "unavailable"},
            }
            for repo_id in GOVERNED_REPOSITORY_IDS
        }

    owns_client = client is None
    active_client = client or httpx.AsyncClient(
        timeout=GITHUB_API_TIMEOUT,
        limits=GITHUB_CLIENT_LIMITS,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"HIVE/{settings.app_version} production-manager",
        },
    )
    branch = settings.repository_github_branch.strip() or "main"
    try:
        results = await asyncio.gather(
            *[
                _github_repo_evidence(active_client, repo_id=repo_id, branch=branch)
                for repo_id in GOVERNED_REPOSITORY_IDS
            ],
            return_exceptions=True,
        )
    finally:
        if owns_client:
            await active_client.aclose()

    evidence: dict[str, dict[str, Any]] = {}
    for repo_id, result in zip(GOVERNED_REPOSITORY_IDS, results, strict=True):
        if isinstance(result, Exception):
            evidence[repo_id] = {
                "configured": True,
                "state": "DEGRADED",
                "reason": f"GitHub evidence probe failed: {result.__class__.__name__}.",
                "sha": None,
                "required_workflows": {},
                "deployment": {"workflow": DEPLOYMENT_WORKFLOWS[repo_id], "status": "unavailable"},
            }
        else:
            evidence[repo_id] = result
    return evidence


async def build_production_manager_report(
    settings: Settings,
    *,
    force_refresh: bool = False,
    github_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    health, gate_evidence = await asyncio.gather(
        build_repo_health_report(settings, force_refresh=force_refresh),
        _collect_all_gate_evidence(settings, client=github_client),
    )

    if health.get("overall_status") == "disabled":
        return {
            "ok": True,
            "role": "ecosystem_production_manager",
            "manager": PRODUCTION_MANAGER,
            "state": "DEGRADED",
            "release_decision": "HOLD",
            "human_action_required": False,
            "reason": "Repository health monitoring is disabled, so HIVE cannot certify ecosystem production state.",
            "governance": production_governance_contract(),
            "repositories": [],
            "health": health,
        }

    health_by_repo = {
        str(item.get("repo")): item
        for item in health.get("repos", [])
        if isinstance(item, dict) and item.get("repo")
    }

    repositories: list[dict[str, Any]] = []
    for repo_id in GOVERNED_REPOSITORY_IDS:
        item = health_by_repo.get(repo_id)
        if item is None:
            health_status = "missing"
            runtime_state = "DEGRADED"
            detail = "Repository is governed but missing from the current health snapshot."
            readiness_status = "missing"
        else:
            health_status = str(item.get("status") or "unknown")
            readiness = item.get("readiness") if isinstance(item.get("readiness"), dict) else {}
            readiness_status = str(readiness.get("status") or "").strip().lower()
            runtime_state = _combine_states(
                _production_state(health_status),
                _production_state(readiness_status) if readiness_status else "GREEN",
            )
            detail = str(item.get("detail") or "")

        evidence = gate_evidence[repo_id]
        state = _combine_states(runtime_state, evidence.get("state") or "DEGRADED")
        repositories.append(
            {
                **_repo_contract(repo_id),
                "state": state,
                "runtime_state": runtime_state,
                "health_status": health_status,
                "readiness_status": readiness_status,
                "detail": detail,
                "gate_evidence": evidence,
                "readiness": (item or {}).get("readiness"),
            }
        )

    if any(item["state"] == "BLOCKED" for item in repositories):
        state = "BLOCKED"
        release_decision = "BLOCK"
    elif any(item["state"] == "DEGRADED" for item in repositories):
        state = "DEGRADED"
        release_decision = "HOLD"
    else:
        state = "GREEN"
        release_decision = "ALLOW"

    return {
        "ok": True,
        "role": "ecosystem_production_manager",
        "manager": PRODUCTION_MANAGER,
        "state": state,
        "release_decision": release_decision,
        "human_action_required": False,
        "reason": (
            "All governed repositories satisfy current runtime, CI/security and exact-SHA deployment checks."
            if state == "GREEN"
            else "One or more governed repositories require remediation or verification before an ecosystem release."
        ),
        "summary": {
            "total": len(repositories),
            "green": sum(1 for item in repositories if item["state"] == "GREEN"),
            "degraded": sum(1 for item in repositories if item["state"] == "DEGRADED"),
            "blocked": sum(1 for item in repositories if item["state"] == "BLOCKED"),
        },
        "governance": production_governance_contract(),
        "repositories": repositories,
        "health": {
            "generated_at": health.get("generated_at"),
            "cached": health.get("cached"),
            "overall_status": health.get("overall_status"),
        },
    }
