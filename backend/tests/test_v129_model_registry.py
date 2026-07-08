from __future__ import annotations

import json

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
    registry.register_model("coding", "persisted-model", score=0.8, provider="openrouter", store=store)

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
