from app.core.governed_repositories import GOVERNED_REPOSITORY_IDS
from app.services.production_manager import REQUIRED_WORKFLOWS, SMOKE_ONLY_WORKFLOWS


def test_workflow_catalogues_cover_governed_estate() -> None:
    assert set(REQUIRED_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert set(SMOKE_ONLY_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert all(not workflows for workflows in SMOKE_ONLY_WORKFLOWS.values())


def test_mast_requires_exact_sha_ci_security_workflows() -> None:
    assert REQUIRED_WORKFLOWS["MAST"] == (
        "MAST CI",
        "Security and repository quality",
        "CodeQL",
    )
    assert SMOKE_ONLY_WORKFLOWS["MAST"] == ()
