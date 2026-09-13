from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import Settings
from app.services.model_governance import ModelUseJustification, lifecycle_status
from app.services.model_router import ModelRouter, TaskType


def _valid_justification() -> ModelUseJustification:
    return ModelUseJustification(
        approval_id="APR-123",
        reason_code="baseline_eval_failure",
        cheaper_model_considered="acme/baseline",
        cheaper_model_insufficiency="Failed 4 of 20 high-risk review cases.",
        evidence="eval-run-2026-09-12",
        expected_calls=20,
        expected_input_tokens=100_000,
        expected_output_tokens=20_000,
        estimated_cost_usd=3.25,
        scope="workflow",
        approver="AI Council owner",
        expires_at=datetime.now(UTC) + timedelta(days=30),
        rollback_model="acme/baseline",
    )


def test_missing_premium_justification_uses_baseline_without_blocking() -> None:
    settings = Settings(default_model="acme/baseline", premium_model="anthropic/claude-opus-5")
    decision = ModelRouter(settings).select_model_decision(
        TaskType.GENERAL,
        "anthropic/claude-opus-5",
    )

    assert decision.selected_model == "acme/baseline"
    assert decision.selection_source == "premium_override_replaced_with_baseline"
    assert decision.justification_issues == ("missing_justification",)


def test_complete_premium_justification_allows_override() -> None:
    settings = Settings(default_model="acme/baseline", premium_model="anthropic/claude-opus-5")
    decision = ModelRouter(settings).select_model_decision(
        TaskType.GENERAL,
        "anthropic/claude-opus-5",
        justification=_valid_justification(),
    )

    assert decision.selected_model == "anthropic/claude-opus-5"
    assert decision.justification_valid is True
    assert decision.selection_source == "approved_premium_override"


def test_free_override_is_public_data_only() -> None:
    settings = Settings(
        default_model="acme/baseline", openrouter_free_fallback_model="acme/free:free"
    )
    router = ModelRouter(settings)

    internal = router.select_model_decision(
        TaskType.GENERAL,
        "acme/free:free",
        data_classification="internal",
    )
    public = router.select_model_decision(
        TaskType.GENERAL,
        "acme/free:free",
        data_classification="public",
    )

    assert internal.selected_model == "acme/baseline"
    assert internal.free_fallback_allowed is False
    assert public.selected_model == "acme/free:free"
    assert public.free_fallback_allowed is True


def test_non_public_fallbacks_exclude_free_and_unapproved_premium() -> None:
    settings = Settings(
        default_model="acme/default",
        cheap_model="acme/cheap",
        balanced_model="acme/balanced",
        premium_model="anthropic/claude-opus-5",
        openrouter_free_fallback_model="acme/free:free",
        allow_paid_fallback=True,
    )
    fallbacks = ModelRouter(settings).fallback_models_for_task(
        TaskType.AUDIT,
        "acme/default",
        allow_free=False,
        allow_premium=False,
    )

    assert "acme/free:free" not in fallbacks
    assert "anthropic/claude-opus-5" not in fallbacks
    assert fallbacks == ["acme/cheap", settings.audit_model, "acme/balanced"]


def test_lifecycle_status_uses_watch_deprecate_and_quarantine_windows() -> None:
    now = datetime(2026, 9, 12, tzinfo=UTC)

    assert lifecycle_status("2026-11-20T00:00:00Z", now=now)[0] == "active"
    assert lifecycle_status("2026-10-20T00:00:00Z", now=now)[0] == "watch"
    assert lifecycle_status("2026-10-01T00:00:00Z", now=now)[0] == "deprecating"
    assert lifecycle_status("2026-09-16T00:00:00Z", now=now)[0] == "quarantined"
    assert lifecycle_status("2026-09-01T00:00:00Z", now=now)[0] == "retired"
