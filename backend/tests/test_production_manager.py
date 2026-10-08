from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import production_manager


def _green_health() -> dict:
    return {
        "ok": True,
        "overall_status": "healthy",
        "generated_at": "2026-10-08T00:00:00+00:00",
        "cached": False,
        "repos": [
            {
                "repo": repo_id,
                "status": "healthy",
                "detail": "ok",
                "readiness": {"status": "ready"},
            }
            for repo_id in production_manager.GOVERNED_REPOSITORY_IDS
        ],
    }


def _green_evidence() -> dict[str, dict]:
    return {
        repo_id: {
            "configured": True,
            "state": "GREEN",
            "reason": "green",
            "sha": "a" * 40,
            "required_workflows": {},
            "deployment": {
                "workflow": production_manager.DEPLOYMENT_WORKFLOWS[repo_id],
                "state": "GREEN",
                "status": "success",
            },
        }
        for repo_id in production_manager.GOVERNED_REPOSITORY_IDS
    }


@pytest.mark.asyncio
async def test_production_manager_is_green_only_when_all_eight_repositories_are_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_health(_settings, *, force_refresh=False):
        assert force_refresh is True
        return _green_health()

    async def fake_evidence(_settings, *, client=None):
        return _green_evidence()

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    monkeypatch.setattr(production_manager, "_collect_all_gate_evidence", fake_evidence)
    report = await production_manager.build_production_manager_report(
        Settings(app_env="test"), force_refresh=True
    )

    assert report["state"] == "GREEN"
    assert report["release_decision"] == "ALLOW"
    assert report["summary"] == {"total": 8, "green": 8, "degraded": 0, "blocked": 0}
    assert report["manager"] == "HIVE"
    assert report["human_action_required"] is False
    assert len(report["governance"]["repositories"]) == 8


@pytest.mark.asyncio
async def test_production_manager_blocks_on_any_down_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = _green_health()
    aims = next(item for item in health["repos"] if item["repo"] == "AIMS")
    aims["status"] = "down"
    aims["detail"] = "offline"
    aims["readiness"] = {"status": "not_ready"}

    async def fake_health(_settings, *, force_refresh=False):
        return health

    async def fake_evidence(_settings, *, client=None):
        return _green_evidence()

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    monkeypatch.setattr(production_manager, "_collect_all_gate_evidence", fake_evidence)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "BLOCKED"
    assert report["release_decision"] == "BLOCK"
    assert report["summary"]["blocked"] == 1
    aims_report = next(item for item in report["repositories"] if item["repository_id"] == "AIMS")
    assert aims_report["state"] == "BLOCKED"
    assert aims_report["remediation_executor"] == "Kilo"
    assert aims_report["readiness_authority"] == "deterministic_ci_security_deployment_runtime_gates"


@pytest.mark.asyncio
async def test_production_manager_blocks_auth_blocked_readiness_even_when_liveness_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = _green_health()
    rams = next(item for item in health["repos"] if item["repo"] == "RAMS")
    rams["status"] = "degraded"
    rams["readiness"] = {"status": "blocked"}

    async def fake_health(_settings, *, force_refresh=False):
        return health

    async def fake_evidence(_settings, *, client=None):
        return _green_evidence()

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    monkeypatch.setattr(production_manager, "_collect_all_gate_evidence", fake_evidence)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "BLOCKED"
    rams_report = next(item for item in report["repositories"] if item["repository_id"] == "RAMS")
    assert rams_report["runtime_state"] == "BLOCKED"
    assert rams_report["readiness_status"] == "blocked"


@pytest.mark.asyncio
async def test_production_manager_holds_when_required_gate_evidence_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _green_evidence()
    evidence["IRS"] = {
        "configured": True,
        "state": "DEGRADED",
        "reason": "missing gate",
        "sha": "b" * 40,
        "required_workflows": {"Production verification": {"state": "DEGRADED", "status": "missing"}},
        "deployment": {"workflow": "IRS Cloudflare Pages deployment watch", "state": "GREEN", "status": "success"},
    }

    async def fake_health(_settings, *, force_refresh=False):
        return _green_health()

    async def fake_evidence(_settings, *, client=None):
        return evidence

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    monkeypatch.setattr(production_manager, "_collect_all_gate_evidence", fake_evidence)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "DEGRADED"
    assert report["release_decision"] == "HOLD"
    irs = next(item for item in report["repositories"] if item["repository_id"] == "IRS")
    assert irs["runtime_state"] == "GREEN"
    assert irs["state"] == "DEGRADED"


@pytest.mark.asyncio
async def test_production_manager_holds_when_governed_repository_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = _green_health()
    health["repos"] = [item for item in health["repos"] if item["repo"] != "IRS"]

    async def fake_health(_settings, *, force_refresh=False):
        return health

    async def fake_evidence(_settings, *, client=None):
        return _green_evidence()

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    monkeypatch.setattr(production_manager, "_collect_all_gate_evidence", fake_evidence)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "DEGRADED"
    assert report["release_decision"] == "HOLD"
    irs = next(item for item in report["repositories"] if item["repository_id"] == "IRS")
    assert irs["health_status"] == "missing"


@pytest.mark.asyncio
async def test_production_manager_holds_when_health_monitoring_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_health(_settings, *, force_refresh=False):
        return {
            "ok": True,
            "overall_status": "disabled",
            "generated_at": "2026-10-08T00:00:00+00:00",
            "cached": False,
            "repos": [],
        }

    async def fake_evidence(_settings, *, client=None):
        return _green_evidence()

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    monkeypatch.setattr(production_manager, "_collect_all_gate_evidence", fake_evidence)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "DEGRADED"
    assert report["release_decision"] == "HOLD"
    assert report["repositories"] == []


def test_production_governance_contract_separates_execution_from_certification() -> None:
    contract = production_manager.production_governance_contract()

    assert contract["manager"] == "HIVE"
    assert contract["separation_of_duties"] == {
        "readiness_authority": "deterministic_ci_security_deployment_runtime_gates",
        "remediation_executor": "Kilo",
        "technical_escalation": "CTO",
        "human_authority": "owner",
        "rule": "The remediation executor must not certify its own repair. Repository gates and live verification remain authoritative.",
    }
    assert "security_policy_exception" in contract["owner_only_triggers"]
