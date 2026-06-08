from __future__ import annotations

from decimal import Decimal, InvalidOperation


def estimate_cost_usd(prompt_tokens: int, completion_tokens: int, model: dict) -> Decimal:
    """Estimate using OpenRouter model pricing fields when available.

    OpenRouter pricing fields are usually per-token decimal strings. If unavailable, return 0.
    """

    pricing = model.get("pricing") or {}
    try:
        prompt_price = Decimal(str(pricing.get("prompt", "0")))
        completion_price = Decimal(str(pricing.get("completion", "0")))
    except (InvalidOperation, TypeError):
        return Decimal("0")
    return (Decimal(prompt_tokens) * prompt_price) + (Decimal(completion_tokens) * completion_price)
