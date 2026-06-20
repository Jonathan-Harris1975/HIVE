from __future__ import annotations

from typing import Any

from app.core.config import Settings

EXECUTION_ADAPTER_ALLOWLIST: list[dict[str, object]] = [
    {
        "adapter_id": "review_approved_handoff",
        "label": "Approved review handoff",
        "scope": "Marks an approved review plan as ready for an operator-triggered production run.",
        "requires_approval": True,
        "mutates_external_systems": False,
    },
    {
        "adapter_id": "evidence_pack_export",
        "label": "Evidence pack export",
        "scope": "Allows approved evidence-pack export responses for HIVE-UI and downstream operator workflows.",
        "requires_approval": True,
        "mutates_external_systems": False,
    },
    {
        "adapter_id": "workflow_simulation",
        "label": "Workflow simulation",
        "scope": "Runs deterministic production preflight simulation before any controlled handoff.",
        "requires_approval": False,
        "mutates_external_systems": False,
    },
]


def execution_adapters_enabled(settings: Settings | Any) -> bool:
    """Return whether the production adapter gate is enabled.

    The setting defaults to enabled because HIVE is now a production instance.
    Approval is still required before a plan becomes executable; the decision
    endpoint never auto-runs side effects.
    """

    return bool(getattr(settings, "execution_adapters_enabled", True))


def execution_adapters_require_approval(settings: Settings | Any) -> bool:
    return bool(getattr(settings, "execution_adapters_require_approval", True))


def execution_adapter_policy(settings: Settings | Any) -> dict[str, object]:
    enabled = execution_adapters_enabled(settings)
    requires_approval = execution_adapters_require_approval(settings)
    return {
        "enabled": enabled,
        "mode": "production_ready" if enabled else "disabled_by_config",
        "requires_approval": requires_approval,
        "allowlist_count": len(EXECUTION_ADAPTER_ALLOWLIST),
        "allowlist": EXECUTION_ADAPTER_ALLOWLIST,
        "can_execute_after_approval": bool(enabled and requires_approval),
        "note": (
            "Execution adapters are available for approved, allow-listed production handoffs."
            if enabled
            else "Execution adapters are disabled by EXECUTION_ADAPTERS_ENABLED=false."
        ),
    }


def approved_execution_payload(settings: Settings | Any, *, approved: bool) -> dict[str, object]:
    policy = execution_adapter_policy(settings)
    can_execute = bool(approved and policy["enabled"])
    return {
        "adapter_execution_enabled": bool(policy["enabled"]),
        "can_execute_now": can_execute,
        "execution_mode": "approved_controlled_execution" if can_execute else "review_gated_execution",
        "execution_state": "ready_for_execution" if can_execute else "awaiting_approval",
        "execution_adapter_policy": policy,
        "execution_handoff": {
            "state": "ready" if can_execute else "waiting",
            "requires_operator_trigger": True,
            "auto_run_on_decision": False,
            "allowlisted_adapters": [item["adapter_id"] for item in EXECUTION_ADAPTER_ALLOWLIST],
        },
    }
