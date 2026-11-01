"""Deterministic recovery diagnosis; no arbitrary execution or model inference."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["ci", "security", "deployment", "runtime", "missed_job", "unknown"]

Step = Literal[
    "inspect_required_checks",
    "inspect_security_report",
    "verify_deployment",
    "inspect_scheduler",
    "inspect_runtime_health",
    "escalate_for_triage",
]

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
    next_step: Step
    automatic_patch_allowed: bool = False


def diagnose_signal(signal: str) -> Diagnosis:
    """Classify an allow-listed signal without interpreting attacker-controlled prose."""
    clean = signal.strip().lower()
    for category, matches in _CATEGORIES:
        if clean in matches or clean.split(":", 1)[-1] in matches:
            step: Step
            if category == "ci":
                step = "inspect_required_checks"
            elif category == "security":
                step = "inspect_security_report"
            elif category == "deployment":
                step = "verify_deployment"
            elif category == "runtime":
                step = "inspect_runtime_health"
            elif category == "missed_job":
                step = "inspect_scheduler"
            else:
                step = "escalate_for_triage"
            return Diagnosis(category, "signal_match", step)
    return Diagnosis("unknown", "unknown", "escalate_for_triage")
