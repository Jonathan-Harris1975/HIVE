from __future__ import annotations

import json

import pytest

from app.core.config import Settings
from app.services import model_registry as registry
from app.services.model_router import Mode, ModelRouter, TaskType


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
