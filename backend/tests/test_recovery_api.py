"""Read-only recovery endpoint contract tests."""
from types import SimpleNamespace

from fastapi import Response

from app.api.recovery import _rows, recovery_status


def test_d1_result_rows():
    assert _rows([{"results": [{"incident_id": "a"}]}]) == [{"incident_id": "a"}]
    assert _rows(None) == []


def test_disabled_recovery_status(monkeypatch):
    class DisabledStore:
        def __init__(self, settings, repository=None):
            self.enabled = False

    monkeypatch.setattr("app.api.recovery.RecoveryD1Store", DisabledStore)
    result = recovery_status(Response(), repository="HIVE", limit=10, settings=SimpleNamespace())
    assert result["ok"] is False
    assert result["mode"] == "disabled"


def test_missing_schema_is_not_reported_as_ready(monkeypatch):
    class EmptyStore:
        enabled = True

        def __init__(self, settings, repository=None):
            self.repository = repository

        def query(self, sql, parameters):
            return {"ok": True, "result": [{"results": []}]}

    monkeypatch.setattr("app.api.recovery.RecoveryD1Store", EmptyStore)
    result = recovery_status(Response(), repository="HIVE", limit=10, settings=SimpleNamespace())
    assert result["schema_ready"] is False
    assert result["mode"] == "inactive"


def test_observation_only_status(monkeypatch):
    class ReadyStore:
        enabled = True

        def __init__(self, settings, repository=None):
            pass

        def query(self, sql, parameters):
            if "sqlite_master" in sql:
                table = "recovery_attempts" if "recovery_attempts" in sql else "recovery_incidents"
                return {"ok": True, "result": [{"results": [{"name": table}]}]}
            return {"ok": True, "result": [{"results": [{"incident_id": "a", "status": "open"}]}]}

    monkeypatch.setattr("app.api.recovery.RecoveryD1Store", ReadyStore)
    result = recovery_status(Response(), repository="HIVE", limit=10, settings=SimpleNamespace())
    assert result["mode"] == "observation_only"
    assert result["count"] == 1


def test_status_selects_repository_recovery_database(monkeypatch):
    captured = {}

    class ReadyStore:
        enabled = True

        def __init__(self, settings, repository=None):
            captured["repository"] = repository

        def query(self, sql, parameters):
            if "sqlite_master" in sql:
                table = "recovery_attempts" if "recovery_attempts" in sql else "recovery_incidents"
                return {"ok": True, "result": [{"results": [{"name": table}]}]}
            return {"ok": True, "result": [{"results": []}]}

    monkeypatch.setattr("app.api.recovery.RecoveryD1Store", ReadyStore)
    result = recovery_status(
        Response(), repository="Jonathan-Harris1975/MAST",
        limit=10, settings=SimpleNamespace(),
    )
    assert captured["repository"] == "MAST"
    assert result["repository"] == "MAST"
    assert result["database_name"] == "mast-autonomous-recovery"
