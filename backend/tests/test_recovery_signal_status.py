from app.services.ops_events import normalise_ops_event


def test_failure_conclusion_maps_to_status_when_absent():
    event = normalise_ops_event({
        "source": "github",
        "service": "Jonathan-Harris1975/HIVE",
        "event_type": "ci_failure",
        "conclusion": "failure",
        "release_id": "a" * 40,
    })
    assert event["status"] == "failure"


def test_explicit_status_wins_over_conclusion():
    event = normalise_ops_event({
        "status": "open",
        "conclusion": "failure",
    })
    assert event["status"] == "open"


def test_severity_does_not_grant_failure_status():
    event = normalise_ops_event({"severity": "critical"})
    assert event["status"] == "open"
