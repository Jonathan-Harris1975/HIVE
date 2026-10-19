from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.ops_events import (
    OpsEventPersistenceError,
    clear_ops_events_for_tests,
    ingest_ops_event,
    list_ops_events,
)
from app.storage.d1 import D1MetadataStore


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


def test_ops_events_can_be_deleted_by_id_or_cleared() -> None:
    clear_ops_events_for_tests()
    settings = Settings(
        app_env="test",
        admin_bearer_token="admin-token-which-is-long-enough",
        ops_event_ingest_enabled=True,
        ops_event_ingest_token="event-token-which-is-long-enough",
        d1_enabled=False,
    )
    admin_headers = {"Authorization": "Bearer admin-token-which-is-long-enough"}
    ingest_headers = {"Authorization": "Bearer event-token-which-is-long-enough"}
    with TestClient(create_app(settings)) as client:
        unauthorised = client.delete("/v1/system/ops-events?event_id=event-1")
        assert unauthorised.status_code == 401

        for event_id in ("event-1", "event-2"):
            response = client.post(
                "/v1/ops/events",
                headers=ingest_headers,
                json={"event_id": event_id, "service": "HIVE"},
            )
            assert response.status_code == 202

        removed = client.delete(
            "/v1/system/ops-events?event_id=event-1",
            headers=admin_headers,
        )
        assert removed.status_code == 200
        assert removed.json()["event_id"] == "event-1"
        remaining = client.get("/v1/system/ops-events", headers=admin_headers)
        assert [item["event_id"] for item in remaining.json()["items"]] == ["event-2"]

        cleared = client.delete(
            "/v1/system/ops-events?clear_all=true",
            headers=admin_headers,
        )
        assert cleared.status_code == 200
        assert cleared.json()["ok"] is True
        assert client.get("/v1/system/ops-events", headers=admin_headers).json()["count"] == 0

        invalid = client.delete("/v1/system/ops-events", headers=admin_headers)
        assert invalid.status_code == 400


def test_persistent_ops_events_do_not_resurrect_stale_worker_memory(monkeypatch) -> None:
    clear_ops_events_for_tests()
    memory_settings = Settings(app_env="test", d1_enabled=False)
    ingest_ops_event(memory_settings, {"event_id": "deleted-on-another-worker", "service": "HIVE"})

    monkeypatch.setattr(D1MetadataStore, "enabled", property(lambda self: True))
    monkeypatch.setattr(
        D1MetadataStore,
        "list_metadata",
        lambda self, *, lane, limit: {"ok": True, "items": []},
    )
    persistent_settings = Settings(
        app_env="test",
        d1_enabled=True,
        d1_account_id="account",
        d1_api_key="token",
        d1_database_id="database",
    )

    result = list_ops_events(persistent_settings)

    assert result["persistent_store"] is True
    assert result["items"] == []
    clear_ops_events_for_tests()


def test_failed_d1_write_is_not_acknowledged_and_same_event_can_be_replayed(monkeypatch) -> None:
    clear_ops_events_for_tests()
    monkeypatch.setattr(D1MetadataStore, "enabled", property(lambda self: True))
    writes = []

    def upsert(self, **kwargs):
        writes.append(kwargs["item_id"])
        return {"ok": len(writes) > 1}

    monkeypatch.setattr(D1MetadataStore, "upsert_metadata", upsert)
    settings = Settings(
        app_env="test",
        ops_event_ingest_enabled=True,
        ops_event_ingest_token="event-token-which-is-long-enough",
        d1_enabled=True,
    )
    event = {"event_id": "stable-event-1", "service": "HIVE", "event_type": "dispatch_failed"}

    with pytest.raises(OpsEventPersistenceError):
        ingest_ops_event(settings, event)

    replay = ingest_ops_event(settings, event)
    assert replay["accepted"] is True
    assert replay["persisted"] is True
    assert writes == ["ops-event:stable-event-1", "ops-event:stable-event-1"]


def test_d1_read_failure_is_not_reported_as_an_empty_healthy_inbox(monkeypatch) -> None:
    monkeypatch.setattr(D1MetadataStore, "enabled", property(lambda self: True))
    monkeypatch.setattr(
        D1MetadataStore,
        "list_metadata",
        lambda self, *, lane, limit: {"ok": False, "message": "temporarily unavailable"},
    )
    settings = Settings(
        app_env="test",
        d1_enabled=True,
        d1_account_id="account",
        d1_api_key="token",
        d1_database_id="database",
    )

    result = list_ops_events(settings)

    assert result["ok"] is False
    assert result["available"] is False
    assert result["persistent_store_error"] == "temporarily unavailable"
    assert result["items"] == []


def test_ops_event_inbox_returns_retryable_503_when_d1_read_fails(monkeypatch) -> None:
    monkeypatch.setattr(D1MetadataStore, "enabled", property(lambda self: True))
    monkeypatch.setattr(
        D1MetadataStore,
        "list_metadata",
        lambda self, *, lane, limit: {"ok": False, "message": "temporarily unavailable"},
    )
    settings = Settings(
        app_env="test",
        admin_bearer_token="admin-token-which-is-long-enough",
        d1_enabled=True,
        d1_account_id="account",
        d1_api_key="token",
        d1_database_id="database",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/v1/system/ops-events",
            headers={"Authorization": "Bearer admin-token-which-is-long-enough"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "state is unknown" in response.json()["detail"]


def test_ops_event_endpoint_returns_retryable_503_when_d1_write_fails(monkeypatch) -> None:
    monkeypatch.setattr(D1MetadataStore, "enabled", property(lambda self: True))
    monkeypatch.setattr(
        D1MetadataStore,
        "upsert_metadata",
        lambda self, **kwargs: {"ok": False, "message": "unavailable"},
    )
    settings = Settings(
        app_env="test",
        admin_bearer_token="admin-token-which-is-long-enough",
        ops_event_ingest_enabled=True,
        ops_event_ingest_token="event-token-which-is-long-enough",
        d1_enabled=True,
        d1_account_id="account",
        d1_api_key="token",
        d1_database_id="database",
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/ops/events",
            headers={"Authorization": "Bearer event-token-which-is-long-enough"},
            json={"event_id": "retry-me", "service": "HIVE"},
        )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "same event ID" in response.json()["detail"]
