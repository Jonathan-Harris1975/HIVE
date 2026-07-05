from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.services import benchmark_engine, model_registry
from app.services.ops_events import ingest_ops_event
from app.services.providers.base import ProviderModelInfo
from app.services.providers.registry import discover_providers
from app.storage.d1 import D1MetadataStore

# Phase 5 - AI Council.
#
# A single run_council() call: discovers every configured provider (Phase 4),
# refreshes their model catalogues, diffs each catalogue against the last
# recorded snapshot (new/retired models), scores every model with the
# Benchmark Engine (Phase 6), and auto-promotes models scoring above
# `ai_council_promotion_threshold` into the Model Registry (Phase 3) for the
# "coding" category. Every run is recorded to D1 (lane="ai_council") for
# optimisation history, and each promotion is pushed through the existing
# ops-event inbox so downstream services (MAST, AIMS) can react.
#
# HONESTY NOTE: real coding/reasoning benchmark scores (e.g. HumanEval,
# SWE-bench) are not available inside this adapter — there is no live
# benchmark data source wired up. Metrics derived here come only from what a
# provider's /models response actually exposes (pricing, context length,
# declared tool/structured-output support) plus this run's own historical
# comparisons. Wire a real benchmark data source into
# `_metrics_for_model` before trusting promotion decisions in production.

LANE = "ai_council"


@dataclass(frozen=True)
class CouncilPromotion:
    category: str
    model_id: str
    score: float
    provider: str


@dataclass(frozen=True)
class CouncilRunReport:
    run_id: str
    occurred_at: str
    providers_discovered: int
    models_seen: int
    new_models: list[str]
    retired_models: list[str]
    promotions: list[CouncilPromotion]
    weights_used: dict[str, float]

    def public_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def _is_coding_candidate(model: ProviderModelInfo, keywords: tuple[str, ...]) -> bool:
    haystack = f"{model.model_id} {model.name}".lower()
    return model.supports_tools and any(keyword in haystack for keyword in keywords if keyword)


def _cost_score(model: ProviderModelInfo) -> float | None:
    """Cheaper = higher score. Normalised against a soft ceiling since actual
    price ranges vary a lot across providers; anything at or above the
    ceiling scores 0.0, free/near-free scores close to 1.0."""
    price = model.pricing_prompt
    if price is None:
        return None
    ceiling = 0.00006  # ~$60 / 1M prompt tokens, a generous soft ceiling
    if price <= 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - (price / ceiling)))


def _long_context_score(model: ProviderModelInfo) -> float | None:
    if not model.context_length:
        return None
    ceiling = 200_000
    return max(0.0, min(1.0, model.context_length / ceiling))


def _metrics_for_model(model: ProviderModelInfo) -> dict[str, float]:
    metrics: dict[str, float] = {}
    cost = _cost_score(model)
    if cost is not None:
        metrics["cost"] = cost
    long_context = _long_context_score(model)
    if long_context is not None:
        metrics["long_context"] = long_context
    metrics["structured_output"] = 1.0 if model.supports_structured_output else 0.0
    # coding_benchmark / reasoning_benchmark / reliability / latency /
    # json_reliability / community_maturity / internal_historical_performance
    # are intentionally left unset here (benchmark_engine.score_model treats
    # missing axes as neutral 0.5) pending a real benchmark data source.
    return metrics


def _previous_snapshot(store: D1MetadataStore, provider_name: str) -> set[str]:
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return set()
    for row in result.get("items", []):
        if row.get("source_type") == "model_catalogue" and row.get("source_id") == provider_name:
            metadata = row.get("metadata") or {}
            return set(metadata.get("model_ids") or [])
    return set()


def _store_snapshot(store: D1MetadataStore, provider_name: str, model_ids: list[str]) -> None:
    store.upsert_metadata(
        item_id=f"ai-council:catalogue:{provider_name}",
        lane=LANE,
        source_type="model_catalogue",
        source_id=provider_name,
        title=f"Model catalogue for {provider_name}",
        url=None,
        metadata={"model_ids": model_ids},
    )


def _record_run_history(store: D1MetadataStore, report: CouncilRunReport, max_entries: int = 200) -> None:
    result = store.list_metadata(lane=LANE, limit=500)
    history: list[dict[str, Any]] = []
    if result.get("ok"):
        for row in result.get("items", []):
            if row.get("source_type") == "run_history" and row.get("source_id") == "runs":
                metadata = row.get("metadata") or {}
                history = list(metadata.get("items") or [])
    history.append(report.public_payload())
    if len(history) > max_entries:
        history = history[-max_entries:]
    store.upsert_metadata(
        item_id="ai-council:run-history",
        lane=LANE,
        source_type="run_history",
        source_id="runs",
        title="AI Council run history",
        url=None,
        metadata={"items": history},
    )


async def run_council(settings: Settings, *, run_id: str | None = None) -> CouncilRunReport:
    store = D1MetadataStore(settings)
    weights = benchmark_engine.load_weights(settings.benchmark_weights_json)
    coding_keywords = tuple(
        keyword.strip().lower()
        for keyword in (settings.ai_council_coding_keywords or "").split(",")
        if keyword.strip()
    )

    providers = discover_providers(settings)
    all_new: list[str] = []
    all_retired: list[str] = []
    promotions: list[CouncilPromotion] = []
    models_seen = 0

    for provider in providers:
        try:
            models = await provider.list_models(force_refresh=True)
        except Exception:  # noqa: BLE001 - one provider failing must not sink the run
            continue

        current_ids = [model.model_id for model in models if model.model_id]
        previous_ids = _previous_snapshot(store, provider.name)
        new_ids = sorted(set(current_ids) - previous_ids)
        retired_ids = sorted(previous_ids - set(current_ids))
        all_new.extend(f"{provider.name}:{model_id}" for model_id in new_ids)
        all_retired.extend(f"{provider.name}:{model_id}" for model_id in retired_ids)
        _store_snapshot(store, provider.name, current_ids)
        models_seen += len(models)

        coding_candidates = [model for model in models if _is_coding_candidate(model, coding_keywords)]
        for model in coding_candidates:
            metrics = _metrics_for_model(model)
            result = benchmark_engine.score_model(metrics, weights=weights)
            if result.score >= settings.ai_council_promotion_threshold:
                model_registry.register_model(
                    "coding", model.model_id, score=result.score, provider=provider.name
                )
                promotions.append(
                    CouncilPromotion(
                        category="coding", model_id=model.model_id, score=result.score, provider=provider.name
                    )
                )

    report = CouncilRunReport(
        run_id=run_id or datetime.now(UTC).strftime("council-%Y%m%dT%H%M%S"),
        occurred_at=datetime.now(UTC).isoformat(),
        providers_discovered=len(providers),
        models_seen=models_seen,
        new_models=all_new,
        retired_models=all_retired,
        promotions=promotions,
        weights_used=weights,
    )
    _record_run_history(store, report)

    for promotion in promotions:
        ingest_ops_event(
            settings,
            {
                "source": "ai_council",
                "service": "hive",
                "event_type": "model_promotion",
                "severity": "info",
                "title": f"{promotion.model_id} promoted to default {promotion.category} model",
                "summary": (
                    f"{promotion.model_id} (provider={promotion.provider}) scored "
                    f"{promotion.score:.3f}, above threshold "
                    f"{settings.ai_council_promotion_threshold:.3f}."
                ),
                "status": "open",
            },
        )

    return report


def get_run_history(settings: Settings, *, limit: int = 20) -> list[dict[str, Any]]:
    store = D1MetadataStore(settings)
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return []
    for row in result.get("items", []):
        if row.get("source_type") == "run_history" and row.get("source_id") == "runs":
            metadata = row.get("metadata") or {}
            items = list(metadata.get("items") or [])
            return items[-limit:]
    return []
