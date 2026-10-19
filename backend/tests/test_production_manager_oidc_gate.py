from app.core.governed_repositories import GOVERNED_REPOSITORY_IDS
from app.services.production_manager import REQUIRED_WORKFLOWS, SMOKE_ONLY_WORKFLOWS


def test_oidc_workflows_are_reported_as_smoke_only_not_production_gates() -> None:
    assert set(REQUIRED_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert set(SMOKE_ONLY_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert all(
        "OIDC" not in workflow
        for workflows in REQUIRED_WORKFLOWS.values()
        for workflow in workflows
    )
    assert all(
        workflows and all("OIDC" in workflow for workflow in workflows)
        for workflows in SMOKE_ONLY_WORKFLOWS.values()
    )


def test_mast_requires_its_exact_sha_estate_refresh_workflow() -> None:
    assert REQUIRED_WORKFLOWS["MAST"] == (
        "MAST CI",
        "Security and repository quality",
        "CodeQL",
    )
    assert SMOKE_ONLY_WORKFLOWS["MAST"] == (
        "OIDC readiness",
        "OIDC estate readiness",
    )
