from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import production_manager


@pytest.mark.asyncio
async def test_production_manager_is_green_only_when_all_eight_repositories_are_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_health(_settings, *, force_refresh=False):
        assert force_refresh is True
        return {
            "ok": True,
            "overall_status": "healthy",
            "generated_at": "2026-10-08T00:00:00+00:00",
            "cached": False,
            "repos": [
                {"repo": repo_id, "status": "healthy", "detail": "ok", "readiness": {"status": "ready"}}
                for repo_id in production_manager.GOVERNED_REPOSITORY_IDS
            ],
        }

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
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
    async def fake_health(_settings, *, force_refresh=False):
        return {
            "ok": True,
            "overall_status": "down",
            "generated_at": "2026-10-08T00:00:00+00:00",
            "cached": False,
            "repos": [
                {
                    "repo": repo_id,
                    "status": "down" if repo_id == "AIMS" else "healthy",
                    "detail": "offline" if repo_id == "AIMS" else "ok",
                    "readiness": {"status": "not_ready" if repo_id == "AIMS" else "ready"},
                }
                for repo_id in production_manager.GOVERNED_REPOSITORY_IDS
            ],
        }

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "BLOCKED"
    assert report["release_decision"] == "BLOCK"
    assert report["summary"]["blocked"] == 1
    aims = next(item for item in report["repositories"] if item["repository_id"] == "AIMS")
    assert aims["state"] == "BLOCKED"
    assert aims["remediation_executor"] == "Kilo"
    assert aims["readiness_authority"] == "deterministic_ci_and_runtime_gates"


@pytest.mark.asyncio
async def test_production_manager_holds_when_governed_repository_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_health(_settings, *, force_refresh=False):
        return {
            "ok": True,
            "overall_status": "healthy",
            "generated_at": "2026-10-08T00:00:00+00:00",
            "cached": False,
            "repos": [
                {"repo": repo_id, "status": "healthy", "detail": "ok", "readiness": {"status": "ready"}}
                for repo_id in production_manager.GOVERNED_REPOSITORY_IDS
                if repo_id != "IRS"
            ],
        }

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
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

    monkeypatch.setattr(production_manager, "build_repo_health_report", fake_health)
    report = await production_manager.build_production_manager_report(Settings(app_env="test"))

    assert report["state"] == "DEGRADED"
    assert report["release_decision"] == "HOLD"
    assert report["repositories"] == []


def test_production_governance_contract_separates_execution_from_certification() -> None:
    contract = production_manager.production_governance_contract()

    assert contract["manager"] == "HIVE"
    assert contract["separation_of_duties"] == {
        "readiness_authority": "deterministic_ci_and_runtime_gates",
        "remediation_executor": "Kilo",
        "technical_escalation": "CTO",
        "human_authority": "owner",
        "rule": "The remediation executor must not certify its own repair. Repository gates and live verification remain authoritative.",
    }
    assert "security_policy_exception" in contract["owner_only_triggers"]
