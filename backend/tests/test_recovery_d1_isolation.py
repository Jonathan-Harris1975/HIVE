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
