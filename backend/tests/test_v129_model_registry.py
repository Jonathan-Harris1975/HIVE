from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import model_registry as registry
from app.services.model_router import ModelRouter, TaskType


@pytest.fixture(autouse=True)
def _clear_registry():
    registry.clear_registry()
    yield
    registry.clear_registry()


def test_register_model_ranks_by_score_descending():
    registry.register_model("coding", "model-a", score=0.5, provider="openrouter")
    registry.register_model("coding", "model-b", score=0.9, provider="openrouter")
    registry.register_model("coding", "model-c", score=0.7, provider="openrouter")

    ranked = registry.get_ranked_models("coding")

    assert [model.model_id for model in ranked] == ["model-b", "model-c", "model-a"]
    assert registry.get_default_model("coding") == "model-b"


def test_register_model_rescoring_replaces_existing_entry():
    registry.register_model("coding", "model-a", score=0.2)
    registry.register_model("coding", "model-a", score=0.95)

    ranked = registry.get_ranked_models("coding")

    assert len(ranked) == 1
    assert ranked[0].score == 0.95


def test_register_model_rejects_unknown_category():
    with pytest.raises(registry.ModelRegistryError):
        registry.register_model("not-a-category", "model-a", score=0.5)


def test_get_default_model_returns_none_when_empty():
    assert registry.get_default_model("reasoning") is None


def test_remove_model():
    registry.register_model("vision", "model-x", score=0.6)
    assert registry.remove_model("vision", "model-x") is True
    assert registry.get_ranked_models("vision") == []
    assert registry.remove_model("vision", "model-x") is False


def test_seed_from_json_populates_multiple_categories():
    seed = json.dumps(
        {
            "coding": [
                {"model_id": "seed-coding-1", "score": 0.8},
                {"model_id": "seed-coding-2", "score": 0.95},
            ],
            "cheap": [{"model_id": "seed-cheap-1", "score": 0.4}],
            "unknown-category": [{"model_id": "ignored", "score": 1}],
        }
    )

    count = registry.seed_from_json(seed)

    assert count == 3
    assert registry.get_default_model("coding") == "seed-coding-2"
    assert registry.get_default_model("cheap") == "seed-cheap-1"


def test_seed_from_json_handles_malformed_input_gracefully():
    assert registry.seed_from_json("") == 0
    assert registry.seed_from_json("not json") == 0
    assert registry.seed_from_json("[]") == 0


def test_model_router_prefers_registry_default_for_code_task():
    settings = Settings(code_model="static-code-model")
    router = ModelRouter(settings)

    assert router.select_model(TaskType.CODE) == "static-code-model"

    registry.register_model("coding", "registry-code-model", score=0.99)

    assert router.select_model(TaskType.CODE) == "registry-code-model"


def test_model_router_explicit_request_still_wins_over_registry():
    settings = Settings()
    registry.register_model("coding", "registry-code-model", score=0.99)
    router = ModelRouter(settings)

    assert router.select_model(TaskType.CODE, requested_model="explicit-model") == "explicit-model"


class _FakeD1Store:
    """Duck-types the subset of D1MetadataStore used by model_registry
    persistence, backed by an in-memory dict instead of a real D1 HTTP
    call, so registry restart-survival can be tested without network."""

    def __init__(self) -> None:
        self.enabled = True
        self._rows: dict[str, dict[str, object]] = {}

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        self._rows[item_id] = {
            "id": item_id,
            "lane": lane,
            "source_type": source_type,
            "source_id": source_id,
            "metadata": metadata,
        }
        return {"ok": True}

    def list_metadata(self, *, lane=None, limit=50):
        items = [row for row in self._rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "count": len(items), "items": items}

    def delete_metadata_ids(self, item_ids):
        for item_id in item_ids:
            self._rows.pop(item_id, None)
        return {"ok": True, "deleted_count": len(item_ids)}


def test_registered_model_survives_simulated_restart_via_d1_store():
    store = _FakeD1Store()
    registry.register_model(
        "coding", "persisted-model", score=0.8, provider="openrouter", store=store
    )

    # Simulate a process restart: the in-memory registry is wiped, then
    # rehydrated from the (fake) D1 store, exactly as app/main.py does at
    # startup via load_registry_from_store().
    registry.clear_registry()
    assert registry.get_default_model("coding") is None

    loaded = registry.load_registry_from_store(store)

    assert loaded == 1
    assert registry.get_default_model("coding") == "persisted-model"


def test_removed_model_is_deleted_from_d1_store_and_not_restored():
    store = _FakeD1Store()
    registry.register_model("cheap", "temp-model", score=0.5, store=store)
    assert registry.remove_model("cheap", "temp-model", store=store) is True

    registry.clear_registry()
    loaded = registry.load_registry_from_store(store)

    assert loaded == 0
    assert registry.get_default_model("cheap") is None


def test_load_registry_from_store_noop_when_disabled_or_missing():
    assert registry.load_registry_from_store(None) == 0

    class _DisabledStore(_FakeD1Store):
        def __init__(self) -> None:
            super().__init__()
            self.enabled = False

    assert registry.load_registry_from_store(_DisabledStore()) == 0


def test_register_model_defaults_new_fields_to_unset():
    ranked = registry.register_model("coding", "model-a", score=0.5)[0]

    assert ranked.benchmark_score is None
    assert ranked.confidence == "unverified"
    assert ranked.latency_ms is None
    assert ranked.cost_per_1k_tokens is None


def test_register_model_accepts_benchmark_confidence_latency_cost():
    ranked = registry.register_model(
        "coding",
        "model-a",
        score=0.9,
        benchmark_score=82.5,
        confidence="measured",
        latency_ms=420.0,
        cost_per_1k_tokens=0.015,
    )[0]

    assert ranked.benchmark_score == 82.5
    assert ranked.confidence == "measured"
    assert ranked.latency_ms == 420.0
    assert ranked.cost_per_1k_tokens == 0.015


def test_register_model_rejects_unknown_confidence_level():
    with pytest.raises(registry.ModelRegistryError):
        registry.register_model("coding", "model-a", score=0.5, confidence="very-sure")


def test_score_alone_still_determines_ranking_regardless_of_benchmark_score():
    # benchmark_score is informational context, not a ranking input - a
    # lower benchmark_score with a higher `score` still wins the category.
    registry.register_model("coding", "model-a", score=0.5, benchmark_score=99.0)
    registry.register_model("coding", "model-b", score=0.9, benchmark_score=10.0)

    assert registry.get_default_model("coding") == "model-b"


def test_seed_from_json_carries_new_fields():
    seed = json.dumps(
        {
            "coding": [
                {
                    "model_id": "seed-coding-1",
                    "score": 0.8,
                    "benchmark_score": 91.2,
                    "confidence": "measured",
                    "latency_ms": 350.0,
                    "cost_per_1k_tokens": 0.02,
                }
            ]
        }
    )

    registry.seed_from_json(seed)
    ranked = registry.get_ranked_models("coding")[0]

    assert ranked.benchmark_score == 91.2
    assert ranked.confidence == "measured"
    assert ranked.latency_ms == 350.0
    assert ranked.cost_per_1k_tokens == 0.02


def test_seed_from_json_falls_back_to_unverified_for_bad_confidence():
    seed = json.dumps({"coding": [{"model_id": "seed-1", "score": 0.5, "confidence": "nonsense"}]})

    registry.seed_from_json(seed)

    assert registry.get_ranked_models("coding")[0].confidence == "unverified"


def test_new_fields_survive_simulated_restart_via_d1_store():
    store = _FakeD1Store()
    registry.register_model(
        "coding",
        "persisted-model",
        score=0.8,
        benchmark_score=77.0,
        confidence="heuristic",
        latency_ms=500.0,
        cost_per_1k_tokens=0.01,
        store=store,
    )

    registry.clear_registry()
    registry.load_registry_from_store(store)
    ranked = registry.get_ranked_models("coding")[0]

    assert ranked.benchmark_score == 77.0
    assert ranked.confidence == "heuristic"
    assert ranked.latency_ms == 500.0
    assert ranked.cost_per_1k_tokens == 0.01


def test_non_routable_lifecycle_is_skipped_without_deleting_history():
    registry.register_model("coding", "retiring-model", score=0.99)
    registry.register_model("coding", "active-model", score=0.90)

    updated = registry.set_model_lifecycle(
        "retiring-model",
        lifecycle_status="quarantined",
        expiration_date="2026-09-15T00:00:00Z",
    )

    assert updated == 1
    assert registry.get_default_model("coding") == "active-model"
    assert registry.get_ranked_models("coding")[0].lifecycle_status == "quarantined"


def test_lifecycle_fields_survive_registry_persistence():
    store = _FakeD1Store()
    registry.register_model(
        "reasoning",
        "acme/model-202609",
        score=0.9,
        canonical_slug="acme/model-202609",
        expiration_date="2026-12-01T00:00:00Z",
        lifecycle_status="watch",
        store=store,
    )

    registry.clear_registry()
    registry.load_registry_from_store(store)
    restored = registry.get_ranked_models("reasoning")[0]

    assert restored.canonical_slug == "acme/model-202609"
    assert restored.expiration_date == "2026-12-01T00:00:00Z"
    assert restored.lifecycle_status == "watch"


def test_model_router_ignores_registry_model_below_quality_floor():
    settings = Settings(code_model="static-code-model", model_registry_min_visible_score=0.72)
    registry.register_model("coding", "low-ranked-code-model", score=0.71)

    router = ModelRouter(settings)

    assert router.select_model(TaskType.CODE) == "static-code-model"


def test_model_router_uses_registry_categories_beyond_coding():
    settings = Settings(
        default_model="static-general",
        audit_model="static-audit",
        model_registry_min_visible_score=0.72,
    )
    registry.register_model("reasoning", "ranked-reasoning", score=0.91)

    router = ModelRouter(settings)

    assert router.select_model(TaskType.GENERAL) == "ranked-reasoning"
    assert router.select_model(TaskType.AUDIT) == "ranked-reasoning"


class _FlakyD1Store(_FakeD1Store):
    def __init__(self, journal_path, *, fail_upserts: int = 0, fail_deletes: int = 0):
        super().__init__()
        self.settings = SimpleNamespace(model_registry_reconciliation_path=str(journal_path))
        self.fail_upserts = fail_upserts
        self.fail_deletes = fail_deletes

    def upsert_metadata(self, **kwargs):
        if self.fail_upserts > 0:
            self.fail_upserts -= 1
            return {"ok": False, "error": "temporary d1 outage"}
        return super().upsert_metadata(**kwargs)

    def delete_metadata_ids(self, item_ids):
        if self.fail_deletes > 0:
            self.fail_deletes -= 1
            return {"ok": False, "error": "temporary d1 outage"}
        return super().delete_metadata_ids(item_ids)


def test_registration_d1_failure_is_visible_and_queued(tmp_path, caplog):
    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)

    with caplog.at_level("ERROR", logger="uvicorn.error.hive.model_registry"):
        registry.register_model("coding", "pending-model", score=0.91, store=store)

    assert registry.get_default_model("coding") == "pending-model"
    state = registry.get_persistence_state("coding", "pending-model")
    assert state["state"] == "pending"
    assert "temporary d1 outage" in str(state["error"])
    assert registry.pending_reconciliation_count() == 1
    assert (tmp_path / "registry-pending.json").exists()
    assert any("model_registry_persistence_failed" in record.message for record in caplog.records)


def test_deletion_d1_failure_is_visible_and_queued(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json")
    registry.register_model("cheap", "delete-me", score=0.8, store=store)
    store.fail_deletes = 1

    assert registry.remove_model("cheap", "delete-me", store=store) is True

    assert registry.get_default_model("cheap") is None
    state = registry.get_persistence_state("cheap", "delete-me")
    assert state["state"] == "pending"
    assert state["action"] == "delete"
    assert registry.pending_reconciliation_count() == 1
    assert "model-registry:cheap:delete-me" in store._rows


def test_reconciliation_recovers_after_temporary_d1_outage_and_is_idempotent(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)
    registry.register_model("reasoning", "recover-me", score=0.88, store=store)

    first = registry.reconcile_pending(store)
    second = registry.reconcile_pending(store)

    assert first["ok"] is True
    assert first["reconciled_count"] == 1
    assert first["pending_count"] == 0
    assert second["attempted_count"] == 0
    assert second["reconciled_count"] == 0
    assert registry.get_persistence_state("reasoning", "recover-me")["state"] == "durable"
    assert "model-registry:reasoning:recover-me" in store._rows
    assert not (tmp_path / "registry-pending.json").exists()


def test_pending_registration_survives_restart_reload_before_reconciliation(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)
    registry.register_model("planning", "restart-model", score=0.93, store=store)

    registry.clear_registry(preserve_pending=True)
    assert registry.get_default_model("planning") is None

    loaded = registry.load_registry_from_store(store)

    assert loaded == 0
    assert registry.get_default_model("planning") == "restart-model"
    assert registry.get_persistence_state("planning", "restart-model")["state"] == "pending"


def test_pending_delete_masks_stale_d1_row_after_restart_then_reconciles(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json")
    registry.register_model("vision", "stale-after-delete", score=0.84, store=store)
    store.fail_deletes = 1
    assert registry.remove_model("vision", "stale-after-delete", store=store) is True
    assert "model-registry:vision:stale-after-delete" in store._rows

    registry.clear_registry(preserve_pending=True)
    loaded = registry.load_registry_from_store(store)

    assert loaded == 1
    assert registry.get_default_model("vision") is None
    assert registry.get_persistence_state("vision", "stale-after-delete")["state"] == "pending"

    result = registry.reconcile_pending(store)

    assert result["reconciled_count"] == 1
    assert result["pending_count"] == 0
    assert "model-registry:vision:stale-after-delete" not in store._rows


def test_persistence_diagnostics_tracks_success_failure_and_recovery(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)
    registry.register_model("research", "metrics-model", score=0.77, store=store)
    before = registry.persistence_diagnostics()
    assert before["pending_count"] == 1
    assert before["metrics"]["persistence_failures"] >= 1

    registry.reconcile_pending(store)
    after = registry.persistence_diagnostics()
    assert after["pending_count"] == 0
    assert after["metrics"]["reconciliation_successes"] >= 1


@pytest.mark.asyncio
async def test_model_registry_api_exposes_pending_persistence_state(tmp_path, monkeypatch):
    from app.api import model_registry as registry_api

    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)
    monkeypatch.setattr(registry_api, "D1MetadataStore", lambda settings: store)

    response = await registry_api.post_register_model(
        "coding",
        registry_api.RegisterModelRequest(model_id="api-pending", score=0.9),
        Settings(),
    )

    assert response["persisted"] is False
    assert response["persistence_state"] == "pending"
    assert response["persistence_pending"] is True
    assert "temporary d1 outage" in str(response["persistence_error"])


def test_newer_success_supersedes_older_pending_mutation(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)
    registry.register_model("coding", "superseded-model", score=0.2, store=store)
    assert registry.pending_reconciliation_count() == 1

    registry.register_model("coding", "superseded-model", score=0.97, store=store)

    assert registry.pending_reconciliation_count() == 0
    assert registry.get_persistence_state("coding", "superseded-model")["state"] == "durable"
    row = store._rows["model-registry:coding:superseded-model"]
    assert row["metadata"]["score"] == 0.97
    assert registry.reconcile_pending(store)["attempted_count"] == 0


def test_concurrent_reconciliation_is_serialised_and_idempotent(tmp_path):
    store = _FlakyD1Store(tmp_path / "registry-pending.json", fail_upserts=1)
    registry.register_model("reasoning", "concurrent-model", score=0.86, store=store)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: registry.reconcile_pending(store), range(2)))

    assert sum(int(result["reconciled_count"]) for result in results) == 1
    assert registry.pending_reconciliation_count() == 0
    assert "model-registry:reasoning:concurrent-model" in store._rows


def test_journal_failure_rejects_mutation_before_d1_or_memory_visibility(tmp_path, monkeypatch):
    store = _FlakyD1Store(tmp_path / "registry-pending.json")

    def fail_journal(_path):
        raise registry.ModelRegistryPersistenceError("journal unavailable")

    monkeypatch.setattr(registry, "_write_pending_journal_locked", fail_journal)

    with pytest.raises(registry.ModelRegistryPersistenceError, match="journal unavailable"):
        registry.register_model("planning", "unsafe-model", score=0.9, store=store)

    assert registry.get_default_model("planning") is None
    assert registry.pending_reconciliation_count() == 0
    assert "model-registry:planning:unsafe-model" not in store._rows


def test_d1_success_with_journal_cleanup_failure_remains_pending(tmp_path, monkeypatch):
    store = _FlakyD1Store(tmp_path / "registry-pending.json")
    original_writer = registry._write_pending_journal_locked
    calls = 0

    def fail_second_write(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise registry.ModelRegistryPersistenceError("journal cleanup unavailable")
        return original_writer(path)

    monkeypatch.setattr(registry, "_write_pending_journal_locked", fail_second_write)

    registry.register_model("research", "cleanup-pending", score=0.81, store=store)

    state = registry.get_persistence_state("research", "cleanup-pending")
    assert state["state"] == "pending"
    assert "journal cleanup" in str(state["error"]).lower()
    assert registry.pending_reconciliation_count() == 1
    assert registry.get_default_model("research") == "cleanup-pending"
    assert "model-registry:research:cleanup-pending" in store._rows
