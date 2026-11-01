"""Deterministic recovery diagnosis; no arbitrary execution or model inference."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["ci", "security", "deployment", "runtime", "missed_job", "unknown"]

_CATEGORIES: tuple[tuple[Category, tuple[str, ...]], ...] = (
    ("security", ("codeql", "vulnerability", "security", "secret_scanning", "dependabot")),
    ("deployment", ("deployment", "deploy", "release", "koyeb", "cloudflare_pages")),
    ("missed_job", ("missed_job", "schedule_missed", "cron_missed")),
    ("ci", ("ci_failure", "workflow_failed", "test_failure", "lint_failure", "build_failed")),
    ("runtime", ("runtime", "healthcheck", "service_down", "unavailable", "crash")),
)


@dataclass(frozen=True)
class Diagnosis:
    category: Category
    confidence: Literal["signal_match", "unknown"]
    next_step: Literal["inspect_required_checks", "inspect_security_report",
                       "verify_deployment", "inspect_scheduler",
                       "inspect_runtime_health", "escalate_for_triage"]
    automatic_patch_allowed: bool = False


def diagnose_signal(signal: str) -> Diagnosis:
    """Classify an allow-listed signal without interpreting attacker-controlled prose."""
    clean = signal.strip().lower()
    for category, matches in _CATEGORIES:
        if clean in matches or clean.split(":", 1)[-1] in matches:
            step = {
                "ci": "inspect_required_checks",
                "security": "inspect_security_report",
                "deployment": "verify_deployment",
                "runtime": "inspect_runtime_health",
                "missed_job": "inspect_scheduler",
            }[category]
            from typing import cast
            return Diagnosis(category, "signal_match", cast(Literal["inspect_required_checks", "inspect_security_report", "verify_deployment", "inspect_scheduler", "inspect_runtime_health", "escalate_for_triage"], step))
    return Diagnosis("unknown", "unknown", "escalate_for_triage")
