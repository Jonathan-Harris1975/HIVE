#!/usr/bin/env python3
"""Fail-closed retry policy for the Kilo webhook handoff."""

from __future__ import annotations

import json
import re
import sys
from typing import Sequence

MAX_REJECTED_ATTEMPTS = 2


def next_action(states: Sequence[str], *, circuit_breaker: str = "open") -> str:
    """Return dispatch:N, hold, or exhausted for this exact run marker."""
    if circuit_breaker.strip().lower() != "open":
        return "circuit-open"
    if "accepted" in states:
        return "accepted"
    if any(state == "pending" or state.startswith("uncertain-") for state in states):
        return "hold"
    rejected = sum(state.startswith("rejected-http-") for state in states)
    if states and states[-1] == "circuit-open":
        if rejected >= MAX_REJECTED_ATTEMPTS:
            return "exhausted"
        return f"dispatch:{rejected + 1}"
    if not states:
        return "dispatch:1"
    latest = states[-1]
    if not latest.startswith("rejected-http-"):
        # Accepted, pending, uncertain, malformed, and unknown are never replayed
        # automatically because the provider may already have accepted the task.
        return "hold"
    if rejected >= MAX_REJECTED_ATTEMPTS:
        return "exhausted"
    return f"dispatch:{rejected + 1}"


def classify_http_status(status: str) -> str:
    """Classify provider outcomes without replaying ambiguous delivery results."""
    clean = status.strip()
    if re.fullmatch(r"2\d\d", clean):
        return "accepted"
    if re.fullmatch(r"4\d\d", clean):
        return f"rejected-http-{clean}"
    if re.fullmatch(r"[1-5]\d\d", clean):
        return f"uncertain-http-{clean}"
    return "uncertain-transport"


def main() -> int:
    if sys.argv[1:2] == ["preflight"] and len(sys.argv) in {3, 4}:
        try:
            states = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            print("hold")
            return 0
        if not isinstance(states, list) or not all(isinstance(item, str) for item in states):
            print("hold")
            return 0
        circuit_breaker = sys.argv[3] if len(sys.argv) == 4 else "open"
        print(next_action(states, circuit_breaker=circuit_breaker))
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "classify":
        print(classify_http_status(sys.argv[2]))
        return 0
    print(
        "usage: kilo_handoff_state.py preflight <states-json> [breaker-state] | classify <http-status>",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
