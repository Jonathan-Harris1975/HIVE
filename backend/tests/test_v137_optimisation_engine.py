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
