from app.core.governed_repositories import GOVERNED_REPOSITORY_IDS
from app.services.production_manager import REQUIRED_WORKFLOWS, SMOKE_ONLY_WORKFLOWS


def test_retired_oidc_readiness_workflow_is_not_catalogued() -> None:
    assert set(REQUIRED_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert set(SMOKE_ONLY_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert all("OIDC readiness" not in workflows for workflows in SMOKE_ONLY_WORKFLOWS.values())
    assert all(
        "OIDC" not in workflow
        for workflows in REQUIRED_WORKFLOWS.values()
        for workflow in workflows
    )


def test_mast_requires_its_exact_sha_estate_refresh_workflow() -> None:
    assert REQUIRED_WORKFLOWS["MAST"] == (
        "MAST CI",
        "Security and repository quality",
        "CodeQL",
    )
    assert SMOKE_ONLY_WORKFLOWS["MAST"] == ("OIDC estate readiness",)
