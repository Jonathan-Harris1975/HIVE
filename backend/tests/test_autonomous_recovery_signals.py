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


def test_real_ci_producer_contracts_reach_correct_repository_incident():
    from app.core.governed_repositories import DEFAULT_GITHUB_SOURCES
    from app.services.ops_events import normalise_ops_event
    coordinator = Mock()
    coordinator.register.return_value = True
    allowed = frozenset(DEFAULT_GITHUB_SOURCES.values())
    for service, repository in DEFAULT_GITHUB_SOURCES.items():
        event = normalise_ops_event({
            "source": "github_actions", "service": service,
            "event_type": "ci_failed", "release_id": "a" * 40,
        })
        result = correlate_failure(event, coordinator, allowed_repositories=allowed)
        assert result["accepted"] is True
        assert coordinator.register.call_args.kwargs["repository"] == repository
        canonical = {**event, "source": "github", "service": repository}
        assert correlate_failure(canonical, coordinator, allowed_repositories=allowed)["incident_id"] == result["incident_id"]


def test_real_deployment_producer_contract_and_explicit_nonfailure():
    from app.services.ops_events import normalise_ops_event
    from app.services.autonomous_recovery_diagnosis import diagnose_signal
    coordinator = Mock()
    coordinator.register.return_value = True
    allowed = frozenset({"Jonathan-Harris1975/HIVE"})
    payload = {"source": "koyeb_deployment_watcher", "service": "HIVE",
               "event_type": "deployment_failed", "release_id": "a" * 40}
    event = normalise_ops_event(payload)
    assert correlate_failure(event, coordinator, allowed_repositories=allowed)["accepted"] is True
    assert diagnose_signal(coordinator.register.call_args.kwargs["signal"]).category == "deployment"
    for extra in ({"status": "success"}, {"status": "open"},
                  {"source": "external"}, {"service": "foreign/HIVE"},
                  {"event_type": "free_text", "severity": "critical"}):
        event = normalise_ops_event({**payload, **extra})
        assert correlate_failure(event, coordinator, allowed_repositories=allowed)["accepted"] is False
