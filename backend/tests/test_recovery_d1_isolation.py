"""Dedicated recovery database routing contracts."""
from app.core.config import Settings
from app.storage.d1 import D1MetadataStore, RecoveryD1Store


def test_recovery_uses_only_its_own_database():
    settings = Settings(D1_ENABLED=True, D1_ACCOUNT_ID="account",
                        D1_API_KEY="token", D1_DATABASE_ID="metadata-id",
                        RECOVERY_D1_DATABASE_ID="recovery-id")
    assert D1MetadataStore(settings).settings.d1_database_id == "metadata-id"
    recovery = RecoveryD1Store(settings)
    assert recovery.enabled is True
    assert recovery.settings.d1_database_id == "recovery-id"
    assert recovery.settings.d1_database_name == "hive-autonomous-recovery"


def test_recovery_fails_closed_without_dedicated_database():
    settings = Settings(D1_ENABLED=True, D1_ACCOUNT_ID="account",
                        D1_API_KEY="token", D1_DATABASE_ID="metadata-id",
                        RECOVERY_D1_DATABASE_ID="")
    recovery = RecoveryD1Store(settings)
    assert recovery.enabled is False
    assert recovery.init_schema()["ok"] is False


def test_provisioned_recovery_database_is_code_default():
    settings = Settings(D1_ENABLED=True, D1_ACCOUNT_ID="account", D1_API_KEY="token")
    assert settings.recovery_d1_database_id == "bf78bd7a-e224-465a-a04e-443822204980"


def test_every_governed_repository_has_its_own_recovery_database():
    from app.core.governed_repositories import DEFAULT_RECOVERY_D1_DATABASES, GOVERNED_REPOSITORY_IDS

    assert set(DEFAULT_RECOVERY_D1_DATABASES) == set(GOVERNED_REPOSITORY_IDS)
    assert len({item["id"] for item in DEFAULT_RECOVERY_D1_DATABASES.values()}) == 8

    settings = Settings(D1_ENABLED=True, D1_ACCOUNT_ID="account", D1_API_KEY="token")
    for repository, database in DEFAULT_RECOVERY_D1_DATABASES.items():
        store = RecoveryD1Store(settings, repository=repository)
        assert store.enabled is True
        assert store.settings.d1_database_id == database["id"]
        assert store.settings.d1_database_name == database["name"]


def test_recovery_database_resolves_full_github_repository_name():
    from app.core.governed_repositories import DEFAULT_RECOVERY_D1_DATABASES

    settings = Settings(D1_ENABLED=True, D1_ACCOUNT_ID="account", D1_API_KEY="token")
    store = RecoveryD1Store(settings, repository="Jonathan-Harris1975/jonathan-harris-website")
    assert store.settings.d1_database_id == DEFAULT_RECOVERY_D1_DATABASES["Website"]["id"]


def test_recovery_rejects_unconfigured_repository():
    import pytest

    settings = Settings(D1_ENABLED=True, D1_ACCOUNT_ID="account", D1_API_KEY="token")
    with pytest.raises(ValueError, match="no configured recovery D1"):
        RecoveryD1Store(settings, repository="outside-org/repo")
