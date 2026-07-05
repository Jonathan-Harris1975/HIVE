from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services import ai_council, model_registry
from app.services.ops_events import clear_ops_events_for_tests, list_ops_events
from app.services.providers.base import ProviderModelInfo


class FakeD1Store:
    """Shared in-memory double for D1MetadataStore, keyed like the real
    hive_ecosystem_metadata table (lane/source_type/source_id)."""

    def __init__(self, _settings=None) -> None:
        self._rows: dict[str, dict] = {}

    def upsert_metadata(self, *, item_id, lane, source_type, source_id, title, url, metadata):
        self._rows[item_id] = {
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
        items = [row for row in self._rows.values() if lane is None or row["lane"] == lane]
        return {"ok": True, "count": len(items), "items": items}


def _model(model_id: str, *, tools: bool = True, context_length: int = 128_000, price: float = 0.000001) -> ProviderModelInfo:
    return ProviderModelInfo(
        model_id=model_id,
        name=model_id,
        context_length=context_length,
        pricing_prompt=price,
        pricing_completion=price * 4,
        supports_tools=tools,
        supports_structured_output=True,
        input_modalities=("text",),
        output_modalities=("text",),
        raw={},
    )


class FakeProvider:
    def __init__(self, name: str, models: list[ProviderModelInfo]) -> None:
        self.name = name
        self._models = models

    async def list_models(self, *, force_refresh: bool = False) -> list[ProviderModelInfo]:
        return self._models

    async def health(self):  # pragma: no cover - not exercised by these tests
        raise NotImplementedError


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    model_registry.clear_registry()
    clear_ops_events_for_tests()
    shared_store = FakeD1Store()
    monkeypatch.setattr(ai_council, "D1MetadataStore", lambda settings: shared_store)
    yield shared_store
    model_registry.clear_registry()
    clear_ops_events_for_tests()


@pytest.fixture
def settings():
    return Settings(ai_council_promotion_threshold=0.6)


@pytest.mark.asyncio
async def test_run_council_promotes_high_scoring_coding_model(monkeypatch, settings):
    good_coder = _model("acme/good-coder", context_length=200_000, price=0.0000001)
    monkeypatch.setattr(
        ai_council, "discover_providers", lambda s: [FakeProvider("acme", [good_coder])]
    )

    report = await ai_council.run_council(settings)

    assert report.providers_discovered == 1
    assert any(p.model_id == "acme/good-coder" for p in report.promotions)
    assert model_registry.get_default_model("coding") == "acme/good-coder"


@pytest.mark.asyncio
async def test_run_council_does_not_promote_non_coding_or_low_scoring_models(monkeypatch, settings):
    non_tool_model = _model("acme/no-tools", tools=False)
    monkeypatch.setattr(
        ai_council, "discover_providers", lambda s: [FakeProvider("acme", [non_tool_model])]
    )

    report = await ai_council.run_council(settings)

    assert report.promotions == []
    assert model_registry.get_default_model("coding") is None


@pytest.mark.asyncio
async def test_run_council_detects_new_and_retired_models_across_runs(monkeypatch, settings):
    monkeypatch.setattr(
        ai_council,
        "discover_providers",
        lambda s: [FakeProvider("acme", [_model("acme/model-a", tools=False)])],
    )
    first = await ai_council.run_council(settings)
    assert "acme:acme/model-a" in first.new_models

    monkeypatch.setattr(
        ai_council,
        "discover_providers",
        lambda s: [FakeProvider("acme", [_model("acme/model-b", tools=False)])],
    )
    second = await ai_council.run_council(settings)

    assert "acme:acme/model-b" in second.new_models
    assert "acme:acme/model-a" in second.retired_models


@pytest.mark.asyncio
async def test_run_council_notifies_downstream_via_ops_events(monkeypatch, settings):
    good_coder = _model("acme/good-coder", context_length=200_000, price=0.0000001)
    monkeypatch.setattr(
        ai_council, "discover_providers", lambda s: [FakeProvider("acme", [good_coder])]
    )

    await ai_council.run_council(settings)

    events = list_ops_events(settings)
    assert any(event.get("event_type") == "model_promotion" for event in events["items"])


@pytest.mark.asyncio
async def test_run_council_records_run_history(monkeypatch, settings):
    monkeypatch.setattr(
        ai_council,
        "discover_providers",
        lambda s: [FakeProvider("acme", [_model("acme/model-a", tools=False)])],
    )
    await ai_council.run_council(settings)
    await ai_council.run_council(settings)

    history = ai_council.get_run_history(settings)
    assert len(history) == 2


@pytest.mark.asyncio
async def test_run_council_survives_a_failing_provider(monkeypatch, settings):
    class BrokenProvider:
        name = "broken"

        async def list_models(self, *, force_refresh: bool = False):
            raise RuntimeError("provider is down")

    good_coder = _model("acme/good-coder", context_length=200_000, price=0.0000001)
    monkeypatch.setattr(
        ai_council,
        "discover_providers",
        lambda s: [BrokenProvider(), FakeProvider("acme", [good_coder])],
    )

    report = await ai_council.run_council(settings)

    assert any(p.model_id == "acme/good-coder" for p in report.promotions)
