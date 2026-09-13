"""Runtime guardrails for governed LLM selection.

The guardrails deliberately fail *open for work* and fail *closed for an
unjustified premium override*: an invalid override is replaced with the
normal Council-approved baseline instead of rejecting the request. Cost
limits are advisory signals only and never stop a workload.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import Settings

DataClassification = Literal["public", "internal", "confidential", "restricted"]
ApprovalScope = Literal["single_request", "workflow", "repository", "standing"]

ROUTABLE_LIFECYCLE_STATUSES = frozenset({"active", "watch"})


class ModelUseJustification(BaseModel):
    """Evidence that must accompany an operator-selected premium model.

    Fields are optional at schema level so an incomplete form does not stop
    the workload with a validation error. ``validate_justification`` reports
    missing evidence and routing safely continues on the baseline model.
    """

    approval_id: str | None = Field(None, max_length=120)
    reason_code: str | None = Field(None, max_length=80)
    cheaper_model_considered: str | None = Field(None, max_length=200)
    cheaper_model_insufficiency: str | None = Field(None, max_length=2_000)
    evidence: str | None = Field(None, max_length=4_000)
    expected_calls: int | None = Field(None, ge=1, le=1_000_000)
    expected_input_tokens: int | None = Field(None, ge=0)
    expected_output_tokens: int | None = Field(None, ge=0)
    estimated_cost_usd: float | None = Field(None, ge=0)
    scope: ApprovalScope | None = None
    approver: str | None = Field(None, max_length=200)
    expires_at: datetime | None = None
    rollback_model: str | None = Field(None, max_length=200)
    emergency: bool = False
    actual_outcome: str | None = Field(None, max_length=4_000)


@dataclass(frozen=True)
class ModelSelectionDecision:
    task: str
    requested_model: str | None
    baseline_model: str
    selected_model: str
    selection_source: str
    premium_override: bool
    justification_required: bool
    justification_valid: bool
    justification_issues: tuple[str, ...]
    policy_issues: tuple[str, ...]
    data_classification: str
    free_fallback_allowed: bool
    budget_control: str = "advisory_only"
    justification: dict[str, object] | None = None

    def public_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["justification_issues"] = list(self.justification_issues)
        payload["policy_issues"] = list(self.policy_issues)
        return payload


def is_premium_model(model_id: str, settings: Settings) -> bool:
    """Return whether a model requires operator override evidence."""

    value = (model_id or "").strip().lower()
    if not value:
        return False
    if value == settings.premium_model.strip().lower():
        return True
    patterns = (
        token.strip().lower() for token in settings.model_governance_premium_patterns.split(",")
    )
    return any(pattern and pattern in value for pattern in patterns)


def validate_justification(
    justification: ModelUseJustification | None,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Validate premium-use evidence without raising or blocking the task."""

    if justification is None:
        return False, ("missing_justification",)

    required_text = {
        "approval_id": justification.approval_id,
        "reason_code": justification.reason_code,
        "cheaper_model_considered": justification.cheaper_model_considered,
        "cheaper_model_insufficiency": justification.cheaper_model_insufficiency,
        "evidence": justification.evidence,
        "scope": justification.scope,
        "approver": justification.approver,
        "rollback_model": justification.rollback_model,
    }
    issues = [
        f"missing_{name}" for name, value in required_text.items() if not str(value or "").strip()
    ]
    if justification.expected_calls is None:
        issues.append("missing_expected_calls")
    if justification.expected_input_tokens is None:
        issues.append("missing_expected_input_tokens")
    if justification.expected_output_tokens is None:
        issues.append("missing_expected_output_tokens")
    if justification.estimated_cost_usd is None:
        issues.append("missing_estimated_cost_usd")
    if justification.expires_at is None:
        issues.append("missing_expires_at")
    else:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        expiry = justification.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        expiry = expiry.astimezone(UTC)
        if expiry <= current:
            issues.append("approval_expired")
        max_seconds = settings.model_governance_approval_max_days * 86_400
        if (expiry - current).total_seconds() > max_seconds:
            issues.append("approval_exceeds_maximum_duration")

    return not issues, tuple(issues)


def free_fallback_allowed(data_classification: str, settings: Settings) -> bool:
    """Free endpoints are public-data-only unless policy explicitly opts out."""

    if not settings.openrouter_free_fallback_public_only:
        return True
    return data_classification == "public"


def select_with_governance(
    *,
    task: str,
    requested_model: str | None,
    baseline_model: str,
    data_classification: str,
    justification: ModelUseJustification | None,
    settings: Settings,
) -> ModelSelectionDecision:
    """Apply premium-override policy while always returning a usable model."""

    requested = (requested_model or "").strip() or None
    premium_override = bool(
        requested and requested != baseline_model and is_premium_model(requested, settings)
    )
    justification_valid = False
    issues: tuple[str, ...] = ()
    selected = baseline_model
    source = "governed_baseline"
    policy_issues: tuple[str, ...] = ()
    free_allowed = free_fallback_allowed(data_classification, settings)
    free_override = bool(
        requested
        and (requested == settings.openrouter_free_fallback_model or requested.endswith(":free"))
        and not free_allowed
    )

    if free_override:
        source = "free_override_replaced_with_baseline"
        policy_issues = ("free_model_not_allowed_for_data_classification",)
    elif requested and (not premium_override or not settings.model_governance_enabled):
        selected = requested
        source = "operator_override"
    elif premium_override and settings.model_governance_enabled:
        justification_valid, issues = validate_justification(justification, settings)
        if justification_valid:
            selected = requested or baseline_model
            source = "approved_premium_override"
        else:
            source = "premium_override_replaced_with_baseline"

    return ModelSelectionDecision(
        task=task,
        requested_model=requested,
        baseline_model=baseline_model,
        selected_model=selected,
        selection_source=source,
        premium_override=premium_override,
        justification_required=premium_override and settings.model_governance_enabled,
        justification_valid=justification_valid,
        justification_issues=issues,
        policy_issues=policy_issues,
        data_classification=data_classification,
        free_fallback_allowed=free_allowed,
        justification=(
            justification.model_dump(mode="json", exclude_none=True)
            if justification is not None
            else None
        ),
    )


def lifecycle_status(
    expiration_date: str | None,
    *,
    now: datetime | None = None,
    watch_days: int = 60,
    deprecating_days: int = 30,
    quarantine_days: int = 7,
) -> tuple[str, int | None]:
    """Translate OpenRouter expiry metadata into an operational state."""

    text = str(expiration_date or "").strip()
    if not text:
        return "active", None
    try:
        expiry = datetime.fromisoformat(text)
    except ValueError:
        return "watch", None
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    seconds = (expiry.astimezone(UTC) - current).total_seconds()
    days = max(0, int(seconds // 86_400))
    if seconds <= 0:
        return "retired", 0
    if seconds <= quarantine_days * 86_400:
        return "quarantined", days
    if seconds <= deprecating_days * 86_400:
        return "deprecating", days
    if seconds <= watch_days * 86_400:
        return "watch", days
    return "active", days


def lifecycle_is_routable(status: str | None) -> bool:
    return str(status or "active").lower() in ROUTABLE_LIFECYCLE_STATUSES
