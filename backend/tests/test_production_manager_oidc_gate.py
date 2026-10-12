from app.core.governed_repositories import GOVERNED_REPOSITORY_IDS
from app.services.production_manager import REQUIRED_WORKFLOWS


def test_every_governed_repository_requires_oidc_readiness_evidence() -> None:
    assert set(REQUIRED_WORKFLOWS) == set(GOVERNED_REPOSITORY_IDS)
    assert all(
        "OIDC readiness" in workflows
        for workflows in REQUIRED_WORKFLOWS.values()
    )
