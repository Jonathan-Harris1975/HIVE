from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.ops_events import clear_ops_events_for_tests


def test_ops_event_ingest_is_separate_from_admin_auth_and_redacts_secrets() -> None:
    clear_ops_events_for_tests()
    settings = Settings(
        app_env="test",
        admin_bearer_token="admin-token-which-is-long-enough",
        ops_event_ingest_enabled=True,
        ops_event_ingest_token="event-token-which-is-long-enough",
        d1_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        rejected = client.post(
            "/v1/ops/events",
            headers={"Authorization": "Bearer admin-token-which-is-long-enough"},
            json={"event_id": "event-1", "service": "RAMS"},
        )
        assert rejected.status_code == 401

        accepted = client.post(
            "/v1/ops/events",
            headers={"Authorization": "Bearer event-token-which-is-long-enough"},
            json={
                "event_id": "event-1",
                "service": "RAMS",
                "severity": "critical",
                "event_type": "deployment_failed",
                "title": "RAMS deployment failed",
                "details": {"api_token": "never-store-this", "status": "error"},
            },
        )
        assert accepted.status_code == 202
        assert accepted.json()["event"]["details"]["api_token"] == "[redacted]"

        listed = client.get(
            "/v1/system/ops-events",
            headers={"Authorization": "Bearer admin-token-which-is-long-enough"},
        )
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["items"][0]["event_id"] == "event-1"


def test_ops_event_ingest_can_be_disabled() -> None:
    settings = Settings(
        app_env="test",
        admin_bearer_token="admin-token-which-is-long-enough",
        ops_event_ingest_enabled=False,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/ops/events",
            headers={"Authorization": "Bearer anything"},
            json={"event_id": "event-2"},
        )
        assert response.status_code == 404
