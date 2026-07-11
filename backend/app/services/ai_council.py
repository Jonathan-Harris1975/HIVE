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


# ---------------------------------------------------------------------------
# Multi-category classification (RC1 fix — Audit Finding #3)
# ---------------------------------------------------------------------------
# The Council previously only populated the "coding" category.  The eight
# remaining categories (reasoning, planning, vision, research, fast, cheap,
# creative, long_context) now have automatic classification paths based on
# model capability signals available in ProviderModelInfo.  Scores remain
# data-driven from the Benchmark Engine; no hardcoded rankings are introduced.


def _is_reasoning_candidate(model: ProviderModelInfo) -> bool:
    """Models with tool support and large context suggest strong reasoning."""
    return model.supports_tools and bool(model.context_length and model.context_length >= 32_000)


def _is_planning_candidate(model: ProviderModelInfo) -> bool:
    """Structured-output support is a strong proxy for planning capability."""
    return model.supports_structured_output and model.supports_tools


def _is_vision_candidate(model: ProviderModelInfo) -> bool:
    """Image in input modalities = vision model."""
    return "image" in model.input_modalities


def _is_research_candidate(model: ProviderModelInfo) -> bool:
    """Long context + tool use enables document-grounded research."""
    return model.supports_tools and bool(model.context_length and model.context_length >= 64_000)


def _is_fast_candidate(model: ProviderModelInfo) -> bool:
    """Fast models: low price as a latency proxy (cheap providers tend to run
    smaller, faster models).  Threshold: prompt price ≤ $2 / 1M tokens."""
    if model.pricing_prompt is None:
        return False
    return model.pricing_prompt <= 0.000_002


def _is_cheap_candidate(model: ProviderModelInfo) -> bool:
    """Cheap models: prompt price ≤ $5 / 1M tokens."""
    if model.pricing_prompt is None:
        return False
    return model.pricing_prompt <= 0.000_005


def _is_creative_candidate(model: ProviderModelInfo) -> bool:
    """Creative models: can produce image/audio output or have 'creative' /
    'instruct' / 'story' in their name.  Also catches general-purpose large
    models (≥128 K context) that tend to excel at creative tasks."""
    if "image" in model.output_modalities or "audio" in model.output_modalities:
        return True
    haystack = f"{model.model_id} {model.name}".lower()
    creative_signals = ("creative", "instruct", "story", "claude", "gpt", "gemini", "llama")
    return any(sig in haystack for sig in creative_signals)


def _is_long_context_candidate(model: ProviderModelInfo) -> bool:
    """Long context models: context window ≥ 64 K tokens."""
    return bool(model.context_length and model.context_length >= 64_000)


# Mapping: category → classifier function
_CATEGORY_CLASSIFIERS: dict[str, object] = {
    "reasoning":    _is_reasoning_candidate,
    "planning":     _is_planning_candidate,
    "vision":       _is_vision_candidate,
    "research":     _is_research_candidate,
    "fast":         _is_fast_candidate,
    "cheap":        _is_cheap_candidate,
    "creative":     _is_creative_candidate,
    "long_context": _is_long_context_candidate,
}


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


def _confidence_label(confidence_fraction: float) -> str:
    """Map benchmark_engine's supplied-axes fraction (0.0-1.0) onto the
    Model Registry's CONFIDENCE_LEVELS. Most axes today are unset pending a
    real benchmark data source (see _metrics_for_model), so this will
    typically land on "heuristic" or "unverified" rather than "measured" -
    that's accurate, not a bug: the promotion score is real and reproducible,
    but it isn't yet backed by measured coding/reasoning benchmarks."""
    if confidence_fraction >= 0.7:
        return "measured"
    if confidence_fraction >= 0.3:
        return "heuristic"
    return "unverified"


def _cost_per_1k(model: ProviderModelInfo) -> float | None:
    """Provider-reported prompt price, converted from per-token to
    per-1,000-tokens for the Model Registry's cost field. None if the
    provider didn't report pricing."""
    if model.pricing_prompt is None:
        return None
    return round(model.pricing_prompt * 1000, 6)


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

        # Classify each model into all applicable categories (coding + 8 others).
        # A single model may be promoted to multiple categories if it qualifies.
        category_candidates: dict[str, list[ProviderModelInfo]] = {
            "coding": [m for m in models if _is_coding_candidate(m, coding_keywords)],
        }
        for cat, classifier in _CATEGORY_CLASSIFIERS.items():
            category_candidates[cat] = [m for m in models if classifier(m)]  # type: ignore[operator]

        # Deduplicate per-model scoring: score once, promote to all qualifying categories.
        scored_cache: dict[str, object] = {}  # model_id -> benchmark result
        for category, candidates in category_candidates.items():
            for model in candidates:
                if model.model_id not in scored_cache:
                    metrics = _metrics_for_model(model)
                    scored_cache[model.model_id] = benchmark_engine.score_model(metrics, weights=weights)
                result = scored_cache[model.model_id]
                if result.score >= settings.ai_council_promotion_threshold:
                    model_registry.register_model(
                        category,
                        model.model_id,
                        score=result.score,
                        provider=provider.name,
                        benchmark_score=round(result.score * 100, 1),
                        confidence=_confidence_label(result.confidence),
                        cost_per_1k_tokens=_cost_per_1k(model),
                        notes=(
                            f"AI Council: {result.confidence * 100:.0f}% of benchmark axes "
                            f"had real signal (rest scored neutral)."
                        ),
                        # Bug fix: `store` was constructed above (line 244) but never
                        # threaded through here, so every monthly promotion was silently
                        # in-memory-only and vanished on the next Koyeb restart even
                        # though load_registry_from_store()/_persist_model() have always
                        # existed and worked correctly for callers that remembered to
                        # pass `store`. This was the persistence gap referenced in
                        # MAST's hive-ai-council-run job notes.
                        store=store,
                    )
                    promotions.append(
                        CouncilPromotion(
                            category=category, model_id=model.model_id, score=result.score, provider=provider.name
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
