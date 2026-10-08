from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.governed_repositories import DEFAULT_GITHUB_SOURCES, GOVERNED_REPOSITORY_IDS
from app.services.repo_health import build_repo_health_report

PRODUCTION_MANAGER = "HIVE"
READINESS_AUTHORITY = "deterministic_ci_and_runtime_gates"
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

_GREEN_HEALTH = {"healthy", "standby", "maintenance"}
_DEGRADED_HEALTH = {"degraded", "starting", "not_configured"}
_BLOCKED_HEALTH = {"down", "blocked", "unavailable", "failed", "error"}


def _production_state(status: object) -> str:
    value = str(status or "").strip().lower()
    if value in _GREEN_HEALTH:
        return "GREEN"
    if value in _DEGRADED_HEALTH:
        return "DEGRADED"
    if value in _BLOCKED_HEALTH:
        return "BLOCKED"
    return "DEGRADED"


def _repo_contract(repo_id: str) -> dict[str, Any]:
    return {
        "repository_id": repo_id,
        "github_repository": DEFAULT_GITHUB_SOURCES[repo_id],
        "ecosystem_manager": PRODUCTION_MANAGER,
        "readiness_authority": READINESS_AUTHORITY,
        "remediation_executor": REMEDIATION_EXECUTOR,
        "technical_escalation": TECHNICAL_ESCALATION,
        "human_authority": HUMAN_AUTHORITY,
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


async def build_production_manager_report(
    settings: Settings,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    health = await build_repo_health_report(settings, force_refresh=force_refresh)

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
            state = "DEGRADED"
            health_status = "missing"
            detail = "Repository is governed but missing from the current health snapshot."
        else:
            health_status = str(item.get("status") or "unknown")
            state = _production_state(health_status)
            detail = str(item.get("detail") or "")

        repositories.append(
            {
                **_repo_contract(repo_id),
                "state": state,
                "health_status": health_status,
                "detail": detail,
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
            "All governed repositories satisfy current deterministic production checks."
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
