#!/usr/bin/env python3
"""Decide whether failed GitHub Actions steps justify waking the Kilo repair agent."""
from __future__ import annotations
import re
import sys

ACTIONABLE_CHECK = re.compile(
    r"\b(test|pytest|vitest|jest|unit|integration|type(?:check)?|mypy|pyright|build|compile|"
    r"schema|migration|lock(?:file)?|dependency|dependencies|audit|vulnerab\w*|codeql|"
    r"trivy|gitleaks|security|actionlint|container image)\b", re.I)
NON_KILO_CHECK = re.compile(
    r"\b(lint|format|prettier|eslint|ruff|autofix|checkout|setup|cache|artifact|upload|"
    r"download|runner|deploy|deployment|koyeb|cloudflare|pages|worker|wait|attestation|"
    r"scorecard)\b", re.I)

def repairable_failed_steps(steps: list[str]) -> list[str]:
    return [step for step in steps if ACTIONABLE_CHECK.search(step) and not NON_KILO_CHECK.search(step)]

def main() -> int:
    steps=[line.strip() for line in sys.stdin if line.strip()]
    actionable=repairable_failed_steps(steps)
    if not actionable:
        print("No repository-repairable failed step identified; Kilo will not be triggered.")
        return 3
    for step in actionable[:12]:
        print(step)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
