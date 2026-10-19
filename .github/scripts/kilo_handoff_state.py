#!/usr/bin/env python3
"""Fail-closed retry policy for the Kilo webhook handoff."""

from __future__ import annotations

import json
import re
import sys
from typing import Sequence

MAX_REJECTED_ATTEMPTS = 2


def next_action(states: Sequence[str]) -> str:
    """Return dispatch:N, hold, or exhausted for this exact run marker."""
    if not states:
        return "dispatch:1"
    if "accepted" in states:
        return "accepted"
    latest = states[-1]
    if not latest.startswith("rejected-http-"):
        # Accepted, pending, uncertain, malformed, and unknown are never replayed
        # automatically because the provider may already have accepted the task.
        return "hold"
    rejected = sum(state.startswith("rejected-http-") for state in states)
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
    if len(sys.argv) != 3 or sys.argv[1] not in {"preflight", "classify"}:
        print(
            "usage: kilo_handoff_state.py preflight <states-json> | classify <http-status>",
            file=sys.stderr,
        )
        return 2
    if sys.argv[1] == "classify":
        print(classify_http_status(sys.argv[2]))
        return 0
    try:
        states = json.loads(sys.argv[2])
    except json.JSONDecodeError:
        print("hold")
        return 0
    if not isinstance(states, list) or not all(isinstance(item, str) for item in states):
        print("hold")
        return 0
    print(next_action(states))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
