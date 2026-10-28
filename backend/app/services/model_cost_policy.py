"""Prefer measured economy candidates only after their task evaluation passes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


def prefer_evaluated_cost(candidates: Sequence[Any]) -> list[Any]:
    """Keep legacy ranking unless available, evaluated task-cost evidence exists.

    Callers must first apply their existing capability, lifecycle and score gates.
    Missing prices are unknown, never zero; list/token prices are not task costs.
    """
    qualified = []
    for candidate in candidates:
        cost = getattr(candidate, "cost_per_successful_task", None)
        sample_size = getattr(candidate, "evaluation_sample_size", None)
        if (
            getattr(candidate, "available", None) is True
            and getattr(candidate, "evaluation_passed", None) is True
            and isinstance(sample_size, int)
            and not isinstance(sample_size, bool)
            and sample_size > 0
            and str(getattr(candidate, "evaluation_evidence", "") or "").strip()
            and isinstance(cost, (int, float))
            and not isinstance(cost, bool)
            and math.isfinite(cost)
            and cost >= 0
        ):
            qualified.append(candidate)
    # Stable sorting preserves the Council's ranking when task costs are equal.
    qualified.sort(key=lambda candidate: candidate.cost_per_successful_task)
    return qualified + [candidate for candidate in candidates if candidate not in qualified]
