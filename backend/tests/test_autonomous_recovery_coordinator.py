"""Recovery coordinator contract tests with no external services."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.autonomous_recovery_coordinator import RecoveryCoordinator, _changed


def test_d1_claim_requires_proven_single_change():
    assert _changed({"ok": True, "result": [{"meta": {"changes": 1}}]})
    assert not _changed({"ok": True, "result": [{"meta": {"changes": 0}}]})
    with pytest.raises(RuntimeError):
        _changed({"ok": True, "result": []})
    with pytest.raises(RuntimeError):
        _changed({"ok": False})


def test_claim_is_revision_scoped():
    d1 = SimpleNamespace(query=Mock(return_value={
        "ok": True, "result": [{"meta": {"changes": 1}}],
    }))
    service = RecoveryCoordinator(d1, SimpleNamespace(), "private")
    assert service.claim(incident_id="id", revision="sha", owner="worker")
    sql, params = d1.query.call_args.args
    assert "revision = ?" in sql
    assert "owner" not in params
    assert "sha" in params


def test_schema_init_fails_closed():
    d1 = SimpleNamespace(enabled=False)
    service = RecoveryCoordinator(d1, SimpleNamespace(), "private")
    assert service.init_schema() is False


def test_receipts_require_private_r2():
    service = RecoveryCoordinator(SimpleNamespace(), SimpleNamespace(write_enabled=False), "private")
    from app.services.autonomous_recovery_evidence import RecoveryReceipt
    with pytest.raises(RuntimeError):
        service.write_receipt(RecoveryReceipt("id", "sha", 1, "held", "0" * 64))


def test_claim_fails_closed_when_attempt_audit_not_written():
    d1 = SimpleNamespace(query=Mock(side_effect=[
        {"ok": True, "result": [{"meta": {"changes": 1}}]},
        {"ok": True, "result": [{"meta": {"changes": 0}}]},
    ]))
    service = RecoveryCoordinator(d1, SimpleNamespace(), "private")
    with pytest.raises(RuntimeError, match="audit"):
        service.claim(incident_id="id", revision="sha", owner="worker")
    assert d1.query.call_count == 2
