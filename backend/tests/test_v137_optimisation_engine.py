from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import optimisation_engine as oe


class FakeD1Store:
    _shared_rows: dict[str, dict] = {}

    def __init__(self, _settings=None) -> None:
        pass

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        FakeD1Store._shared_rows[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "title": title,
            "url": url,
            "metadata": metadata,
        }
        return {"ok": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [row for row in FakeD1Store._shared_rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "count": len(items), "items": items}


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch):
    FakeD1Store._shared_rows = {}
    monkeypatch.setattr(oe, "D1MetadataStore", FakeD1Store)
    yield


@pytest.fixture
def settings():
    return Settings()


def test_record_decision_returns_applied_status(settings):
    decision = oe.record_decision(
        settings,
        decision_type="model_promotion",
        description="Promoted acme/coder to default coding model",
        previous_state={"default_coding_model": "old-model"},
        new_state={"default_coding_model": "acme/coder"},
        confidence=0.9,
    )
    assert decision["status"] == "applied"
    assert decision["confidence"] == 0.9


def test_rollback_decision_marks_reverted_and_is_idempotent(settings):
    decision = oe.record_decision(
        settings,
        decision_type="model_promotion",
        description="test",
        previous_state="a",
        new_state="b",
        confidence=0.5,
    )
    rolled_back = oe.rollback_decision(settings, decision["decision_id"])
    assert rolled_back["status"] == "reverted"
    assert rolled_back["reverted_at"] is not None

    # Idempotent: rolling back again doesn't error or change reverted_at meaning.
    rolled_back_again = oe.rollback_decision(settings, decision["decision_id"])
    assert rolled_back_again["status"] == "reverted"


def test_rollback_unknown_decision_raises(settings):
    with pytest.raises(oe.OptimisationEngineError):
        oe.rollback_decision(settings, "does-not-exist")


def test_list_decisions_filters_by_type(settings):
    oe.record_decision(settings, decision_type="a", description="x", previous_state=None, new_state=None, confidence=0.5)
    oe.record_decision(settings, decision_type="b", description="y", previous_state=None, new_state=None, confidence=0.5)

    filtered = oe.list_decisions(settings, decision_type="a")
    assert len(filtered) == 1
    assert filtered[0]["decision_type"] == "a"


def test_success_rate_report_reflects_rollbacks_and_experiments(settings):
    d1 = oe.record_decision(settings, decision_type="x", description="1", previous_state=None, new_state=None, confidence=0.5)
    oe.record_decision(settings, decision_type="x", description="2", previous_state=None, new_state=None, confidence=0.5)
    oe.rollback_decision(settings, d1["decision_id"])

    oe.record_experiment(settings, name="exp1", hypothesis="h", outcome="o", success=True)
    oe.record_experiment(settings, name="exp2", hypothesis="h", outcome="o", success=False)

    stats = oe.success_rate_report(settings)
    assert stats["decision_count"] == 2
    assert stats["reverted_count"] == 1
    assert stats["experiment_count"] == 2
    assert stats["experiment_success_rate"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# RAMS QA-event ingestion adapter — service-level tests.
# ---------------------------------------------------------------------------


def test_ingest_qa_event_records_a_decision(settings):
    payload = {
        "event_id": "qa-evt-001",
        "category": "skill_review",
        "subject_id": "skill:acme/formatter",
        "qa_score": 0.82,
        "recommendation": "Promote to stable lane",
    }

    result = oe.ingest_qa_event(settings, payload)

    assert result["_ingested"] is True
    assert result["decision_type"] == "rams_qa_event"
    assert result["confidence"] == pytest.approx(0.82)
    assert result["source_event_id"] == "qa-evt-001"
    assert result["status"] == "applied"


def test_ingest_qa_event_is_idempotent_on_event_id(settings):
    payload = {
        "event_id": "qa-evt-002",
        "category": "workflow_check",
        "subject_id": "workflow:nightly-build",
        "qa_score": 0.5,
        "recommendation": "No action needed",
    }

    first = oe.ingest_qa_event(settings, payload)
    second = oe.ingest_qa_event(settings, payload)

    assert first["_ingested"] is True
    assert second["_ingested"] is False
    assert first["decision_id"] == second["decision_id"]
    assert len(oe.list_decisions(settings, decision_type="rams_qa_event")) == 1


def test_ingest_qa_event_rejects_missing_fields(settings):
    with pytest.raises(oe.QAEventValidationError):
        oe.ingest_qa_event(settings, {"event_id": "qa-evt-003"})


def test_ingest_qa_event_rejects_out_of_range_score(settings):
    with pytest.raises(oe.QAEventValidationError):
        oe.ingest_qa_event(
            settings,
            {
                "event_id": "qa-evt-004",
                "category": "skill_review",
                "subject_id": "skill:x",
                "qa_score": 1.5,
                "recommendation": "n/a",
            },
        )


def test_ingested_qa_event_is_reflected_in_decisions_list_and_stats(settings):
    """This is the audit's required end-to-end proof: a QA event ingested
    through the real ingestion adapter must be retrievable both from the
    decisions list and from the success-rate stats endpoint."""
    oe.ingest_qa_event(
        settings,
        {
            "event_id": "qa-evt-005",
            "category": "repository_review",
            "subject_id": "repo:HIVE",
            "qa_score": 0.91,
            "recommendation": "No blocking issues found",
        },
    )

    decisions = oe.list_decisions(settings, decision_type="rams_qa_event")
    assert len(decisions) == 1
    assert decisions[0]["source_event_id"] == "qa-evt-005"

    stats = oe.success_rate_report(settings)
    assert stats["decision_count"] == 1
    assert stats["applied_count"] == 1


# ---------------------------------------------------------------------------
# Full HTTP round trip: RAMS posts a QA event through its own token-gated
# endpoint, and an operator retrieves it via the admin-gated decisions and
# stats endpoints. Exercises app.main.create_app wiring end to end so the
# router registration itself is covered, not just the service function.
# ---------------------------------------------------------------------------


def test_qa_event_http_ingestion_is_visible_via_admin_decisions_and_stats_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr("app.services.optimisation_engine.D1MetadataStore", FakeD1Store)

    app_settings = Settings(
        ADMIN_BEARER_TOKEN="a" * 48,
        RAMS_QA_INGEST_ENABLED=True,
        RAMS_QA_INGEST_TOKEN="r" * 40,
        REPO_HEALTH_ENABLED=False,
    )
    client = TestClient(create_app(app_settings))

    ingest_response = client.post(
        "/v1/optimisation/qa-events",
        headers={"Authorization": f"Bearer {'r' * 40}"},
        json={
            "event_id": "qa-evt-http-001",
            "category": "skill_review",
            "subject_id": "skill:acme/linter",
            "qa_score": 0.77,
            "recommendation": "Ship it",
        },
    )
    assert ingest_response.status_code == 202
    assert ingest_response.json()["_ingested"] is True

    decisions_response = client.get(
        "/v1/optimisation/decisions",
        params={"decision_type": "rams_qa_event"},
        headers={"Authorization": f"Bearer {'a' * 48}"},
    )
    assert decisions_response.status_code == 200
    decisions = decisions_response.json()["decisions"]
    assert any(d["source_event_id"] == "qa-evt-http-001" for d in decisions)

    stats_response = client.get(
        "/v1/optimisation/stats", headers={"Authorization": f"Bearer {'a' * 48}"}
    )
    assert stats_response.status_code == 200
    assert stats_response.json()["decision_count"] >= 1

    # Wrong token is rejected, and ingestion is unreachable without the
    # RAMS-specific token even if the caller has the HIVE admin token.
    rejected = client.post(
        "/v1/optimisation/qa-events",
        headers={"Authorization": f"Bearer {'a' * 48}"},
        json={
            "event_id": "qa-evt-http-002",
            "category": "skill_review",
            "subject_id": "skill:x",
            "qa_score": 0.5,
            "recommendation": "n/a",
        },
    )
    assert rejected.status_code == 401
