from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import uuid
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
# `ai_council_promotion_threshold` into the Model Registry (Phase 3) only when
# the evidence coverage also meets `ai_council_auto_promotion_min_confidence`.
# Every run is recorded to D1 (lane="ai_council") for
# optimisation history, and each promotion is pushed through the existing
# ops-event inbox so downstream services (MAST, AIMS) can react.
#
# Benchmark quality is sourced from OpenRouter's authenticated /benchmarks
# endpoint when the primary OpenRouter adapter is present. Providers that do
# not expose measured benchmark data still participate in discovery, but their
# catalogue-only confidence remains below the automatic-promotion gate.

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
    benchmark_sources: list[dict[str, Any]] = field(default_factory=list)

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


def _metrics_for_model(
    model: ProviderModelInfo, benchmark: dict[str, Any] | None = None
) -> dict[str, float]:
    metrics: dict[str, float] = {}
    cost = _cost_score(model)
    if cost is not None:
        metrics["cost"] = cost
    long_context = _long_context_score(model)
    if long_context is not None:
        metrics["long_context"] = long_context
    metrics["structured_output"] = 1.0 if model.supports_structured_output else 0.0

    # OpenRouter's unified benchmark endpoint exposes Artificial Analysis
    # coding/intelligence/agentic composite indices. These are measured external
    # quality signals, not percentages of a theoretical 100-point ceiling.
    # _benchmark_map therefore population-normalises them before scoring.
    if benchmark:
        coding_index = benchmark.get("coding_index")
        intelligence_index = benchmark.get("intelligence_index")
        agentic_index = benchmark.get("agentic_index")
        try:
            if coding_index is not None:
                metrics["coding_benchmark"] = float(
                    benchmark.get("_coding_normalised", float(coding_index) / 100.0)
                )
            if intelligence_index is not None:
                metrics["reasoning_benchmark"] = float(
                    benchmark.get("_reasoning_normalised", float(intelligence_index) / 100.0)
                )
            if agentic_index is not None:
                # Agentic performance is the best externally measured proxy
                # available for real tool/workflow execution. It is recorded
                # as reliability evidence rather than pretending it is an
                # internal HIVE benchmark.
                metrics["reliability"] = float(
                    benchmark.get("_agentic_normalised", float(agentic_index) / 100.0)
                )
        except (TypeError, ValueError):
            pass
    return metrics


def _percentile_scores(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    """Return 0..1 empirical percentile scores for one benchmark index.

    Artificial Analysis exposes composite indices, not percentages.  Treating an
    index such as 74.9 as ``0.749 of a theoretical 100`` makes HIVE's 0.72
    promotion floor effectively unreachable once the other neutral/missing axes
    are included.  The Council instead normalises each current benchmark feed
    relative to the population returned by OpenRouter, preserving measured
    ordering without inventing an absolute scale.
    """
    values: list[tuple[str, float]] = []
    for item in items:
        model_id = str(item.get("model_permaslug") or "").strip()
        if not model_id:
            continue
        try:
            raw = item.get(key)
            if raw is None:
                continue
            values.append((model_id, float(raw)))
        except (TypeError, ValueError):
            continue
    # A tiny partial feed is not enough evidence for population normalisation.
    # Fall back to the conservative raw-index path in _metrics_for_model instead
    # of turning a lone response into an artificial perfect score.
    if len(values) < 5:
        return {}
    ordered = sorted(value for _, value in values)
    scores: dict[str, float] = {}
    denominator = len(ordered) - 1
    for model_id, value in values:
        # Average the first/last rank for ties so equal benchmark values remain equal.
        first = ordered.index(value)
        last = len(ordered) - 1 - ordered[::-1].index(value)
        scores[model_id] = ((first + last) / 2.0) / denominator
    return scores


def _benchmark_map(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index benchmark rows and attach population-normalised quality signals."""
    coding = _percentile_scores(items, "coding_index")
    reasoning = _percentile_scores(items, "intelligence_index")
    agentic = _percentile_scores(items, "agentic_index")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        model_id = str(item.get("model_permaslug") or "").strip()
        if not model_id:
            continue
        enriched = dict(item)
        if model_id in coding:
            enriched["_coding_normalised"] = coding[model_id]
        if model_id in reasoning:
            enriched["_reasoning_normalised"] = reasoning[model_id]
        if model_id in agentic:
            enriched["_agentic_normalised"] = agentic[model_id]
        result[model_id] = enriched
    return result


def _confidence_label(confidence_fraction: float) -> str:
    """Map benchmark coverage onto the Model Registry confidence vocabulary.

    OpenRouter/Artificial Analysis supplies measured coding, intelligence and
    agentic evidence when available. The remaining optional axes may still be
    neutral, so the label describes coverage rather than pretending every
    dimension was independently benchmarked.
    """
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


def _benchmark_snapshot_id(provider_name: str, source: str) -> str:
    return f"ai-council:benchmark:{provider_name}:{source}"


def _store_benchmark_snapshot(
    store: D1MetadataStore,
    *,
    provider_name: str,
    source: str,
    items: list[dict[str, Any]],
) -> bool:
    """Persist the last-known-good measured benchmark feed in D1.

    The cache is deliberately only written for non-empty successful feeds. An
    empty/transient provider response must never replace useful measured data.
    """
    if not items:
        return False
    result = store.upsert_metadata(
        item_id=_benchmark_snapshot_id(provider_name, source),
        lane=LANE,
        source_type="benchmark_snapshot",
        source_id=f"{provider_name}:{source}",
        title=f"Benchmark snapshot {provider_name}/{source}",
        url=None,
        metadata={
            "provider": provider_name,
            "source": source,
            "fetched_at": datetime.now(UTC).isoformat(),
            "items": items,
        },
    )
    return bool(result.get("ok"))


def _load_benchmark_snapshot(
    store: D1MetadataStore,
    *,
    provider_name: str,
    source: str,
    max_age_days: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return a bounded-age measured benchmark snapshot plus diagnostics."""
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return [], {"ok": False, "reason": "benchmark cache unavailable"}

    expected_id = f"{provider_name}:{source}"
    rows = result.get("items") if isinstance(result.get("items"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("source_type") != "benchmark_snapshot" or row.get("source_id") != expected_id:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        raw_items = metadata.get("items")
        items = [dict(item) for item in raw_items if isinstance(item, dict)] if isinstance(raw_items, list) else []
        fetched_text = str(metadata.get("fetched_at") or row.get("updated_at") or "").strip()
        try:
            fetched_at = datetime.fromisoformat(fetched_text.replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            fetched_at = fetched_at.astimezone(UTC)
        except ValueError:
            return [], {"ok": False, "reason": "benchmark cache timestamp is invalid"}

        age = datetime.now(UTC) - fetched_at
        if age < timedelta(0) or age > timedelta(days=max(1, max_age_days)):
            return [], {
                "ok": False,
                "reason": "benchmark cache is stale",
                "fetched_at": fetched_at.isoformat(),
                "age_days": round(max(0.0, age.total_seconds()) / 86400.0, 2),
            }
        if not items:
            return [], {"ok": False, "reason": "benchmark cache is empty"}
        return items, {
            "ok": True,
            "fetched_at": fetched_at.isoformat(),
            "age_days": round(age.total_seconds() / 86400.0, 2),
            "item_count": len(items),
        }
    return [], {"ok": False, "reason": "no benchmark cache exists"}


async def _load_provider_benchmarks(
    settings: Settings,
    store: D1MetadataStore,
    provider: Any,
    *,
    source: str = "artificial-analysis",
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Load measured benchmarks with bounded retry and last-good fallback.

    Council used to swallow every benchmark exception and silently continue
    with catalogue-only evidence.  That turned a single OpenRouter timeout into
    zero qualified models while leaving operators with no useful diagnostic.
    This helper keeps the fail-closed promotion rule but makes the evidence
    retrieval itself resilient and observable.
    """
    loader = getattr(provider, "list_benchmarks", None)
    provider_name = str(getattr(provider, "name", "unknown"))
    if not callable(loader):
        return {}, {
            "provider": provider_name,
            "source": source,
            "ok": False,
            "mode": "unsupported",
            "item_count": 0,
            "error": "provider does not expose measured benchmarks",
        }

    attempts = max(1, int(settings.ai_council_benchmark_attempts))
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            raw = await loader(source=source)
            items = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
            if not items:
                raise RuntimeError("benchmark feed returned no rows")
            cache_written = _store_benchmark_snapshot(
                store,
                provider_name=provider_name,
                source=source,
                items=items,
            )
            return _benchmark_map(items), {
                "provider": provider_name,
                "source": source,
                "ok": True,
                "mode": "live",
                "attempts": attempt,
                "item_count": len(items),
                "cache_written": cache_written,
            }
        except Exception as exc:  # noqa: BLE001 - fallback is deliberate
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < attempts:
                delay = max(0.0, float(settings.ai_council_benchmark_retry_base_seconds)) * attempt
                if delay:
                    await asyncio.sleep(delay)

    cached, cache_status = _load_benchmark_snapshot(
        store,
        provider_name=provider_name,
        source=source,
        max_age_days=settings.ai_council_benchmark_cache_max_age_days,
    )
    if cached:
        return _benchmark_map(cached), {
            "provider": provider_name,
            "source": source,
            "ok": True,
            "mode": "cache",
            "attempts": attempts,
            "item_count": len(cached),
            "live_error": last_error,
            **cache_status,
        }

    return {}, {
        "provider": provider_name,
        "source": source,
        "ok": False,
        "mode": "unavailable",
        "attempts": attempts,
        "item_count": 0,
        "error": last_error or "benchmark feed unavailable",
        "cache": cache_status,
    }


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


def record_run_completion(
    settings: Settings,
    *,
    run_id: str,
    completion_status: str,
    downstream_sync: dict[str, Any],
    max_entries: int = 200,
) -> bool:
    """Attach downstream completion state to an already-recorded Council run.

    ``run_council`` deliberately records its evidence/promotion result before
    downstream propagation.  The API layer calls this helper after the AIMS/RAMS
    sync attempt so Monthly Review can distinguish a completed Council cycle from
    a Council run whose propagation failed.
    """
    store = D1MetadataStore(settings)
    result = store.list_metadata(lane=LANE, limit=500)
    if not result.get("ok"):
        return False

    history: list[dict[str, Any]] = []
    raw_rows = result.get("items")
    rows = raw_rows if isinstance(raw_rows, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("source_type") == "run_history" and row.get("source_id") == "runs":
            metadata = row.get("metadata") or {}
            if isinstance(metadata, dict):
                raw_history = metadata.get("items")
                history = [dict(item) for item in raw_history if isinstance(item, dict)] if isinstance(raw_history, list) else []
            break

    updated = False
    for item in reversed(history):
        if str(item.get("run_id") or "") == run_id:
            item["completion_status"] = completion_status
            item["downstream_sync"] = dict(downstream_sync)
            item["completed_at"] = datetime.now(UTC).isoformat()
            updated = True
            break
    if not updated:
        return False

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
    return True


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
    benchmark_sources: list[dict[str, Any]] = []
    models_seen = 0

    for provider in providers:
        try:
            models = await provider.list_models(force_refresh=True)
        except Exception:  # noqa: BLE001 - one provider failing must not sink the run
            continue

        benchmark_by_model, benchmark_status = await _load_provider_benchmarks(
            settings,
            store,
            provider,
            source="artificial-analysis",
        )
        benchmark_sources.append(benchmark_status)

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
            # A measured coding index is stronger evidence of coding capability
            # than a model name containing "code". Keep keyword classification
            # as a fallback for providers without benchmark coverage.
            "coding": [
                m
                for m in models
                if _is_coding_candidate(m, coding_keywords)
                or benchmark_by_model.get(m.model_id, {}).get("coding_index") is not None
            ],
        }
        for cat, classifier in _CATEGORY_CLASSIFIERS.items():
            category_candidates[cat] = [m for m in models if classifier(m)]  # type: ignore[operator]

        # Deduplicate per-model scoring: score once, promote to all qualifying categories.
        scored_cache: dict[str, object] = {}  # model_id -> benchmark result
        for category, candidates in category_candidates.items():
            for model in candidates:
                if model.model_id not in scored_cache:
                    metrics = _metrics_for_model(model, benchmark_by_model.get(model.model_id))
                    scored_cache[model.model_id] = benchmark_engine.score_model(metrics, weights=weights)
                result = scored_cache[model.model_id]
                if (
                    result.score >= settings.ai_council_promotion_threshold
                    and result.confidence >= settings.ai_council_auto_promotion_min_confidence
                ):
                    model_registry.register_model(
                        category,
                        model.model_id,
                        score=result.score,
                        provider=provider.name,
                        benchmark_score=(
                            round(float(benchmark_by_model[model.model_id].get("coding_index")
                                        or benchmark_by_model[model.model_id].get("intelligence_index")
                                        or result.score * 100), 1)
                            if model.model_id in benchmark_by_model
                            else round(result.score * 100, 1)
                        ),
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
        run_id=run_id or f"council-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex[:6]}",
        occurred_at=datetime.now(UTC).isoformat(),
        providers_discovered=len(providers),
        models_seen=models_seen,
        new_models=all_new,
        retired_models=all_retired,
        promotions=promotions,
        weights_used=weights,
        benchmark_sources=benchmark_sources,
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
                    f"{promotion.score:.3f}, above score threshold "
                    f"{settings.ai_council_promotion_threshold:.3f} with sufficient benchmark coverage "
                    f"(minimum confidence {settings.ai_council_auto_promotion_min_confidence:.2f})."
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
