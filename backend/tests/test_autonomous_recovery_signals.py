from unittest.mock import Mock

from app.services.autonomous_recovery_signals import correlate_failure


def _event(**overrides):
    event = {
        "source": "github",
        "service": "Jonathan-Harris1975/HIVE",
        "release_id": "a" * 40,
        "status": "failed",
        "environment": "production",
        "event_type": "ci_failure",
    }
    event.update(overrides)
    return event


def test_trusted_production_failure_is_deduplicated_by_identity():
    coordinator = Mock()
    coordinator.register.return_value = True
    allowed = frozenset({"Jonathan-Harris1975/HIVE"})
    first = correlate_failure(_event(), coordinator, allowed_repositories=allowed)
    second = correlate_failure(_event(), coordinator, allowed_repositories=allowed)
    assert first["accepted"] is True
    assert first["incident_id"] == second["incident_id"]
    assert coordinator.register.call_count == 2


def test_authenticated_aims_runtime_failure_is_eligible_only_for_canonical_aims_and_exact_sha():
    coordinator = Mock()
    coordinator.register.return_value = True
    allowed = frozenset({"Jonathan-Harris1975/AIMS"})
    event = _event(
        source="aims_runtime",
        service="Jonathan-Harris1975/AIMS",
        event_type="scheduled_workflow_failed",
    )
    result = correlate_failure(event, coordinator, allowed_repositories=allowed)
    assert result["accepted"] is True
    assert coordinator.register.call_args.kwargs["repository"] == "Jonathan-Harris1975/AIMS"

    for changes in (
        {"service": "Jonathan-Harris1975/HIVE"},
        {"release_id": "version-2.14.1"},
    ):
        assert correlate_failure({**event, **changes}, coordinator,
                                 allowed_repositories=allowed)["accepted"] is False


def test_ineligible_signals_cannot_create_incidents():
    coordinator = Mock()
    allowed = frozenset({"Jonathan-Harris1975/HIVE"})
    for changes in (
        {"source": "external"},
        {"environment": "staging"},
        {"service": "unknown/repo"},
        {"release_id": "not-a-sha"},
        {"status": "success"},
        {"event_type": ""},
    ):
        assert correlate_failure(_event(**changes), coordinator,
                                 allowed_repositories=allowed)["accepted"] is False
    coordinator.register.assert_not_called()
