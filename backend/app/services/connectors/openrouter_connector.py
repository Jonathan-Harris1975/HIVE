from __future__ import annotations

from app.core.config import Settings
from app.services.connectors.base import ConnectorReport
from app.services.providers.openrouter_provider import OpenRouterProvider


async def report(settings: Settings) -> ConnectorReport:
    configured = bool(settings.openrouter_api_key)
    if not configured:
        return ConnectorReport(
            name="openrouter",
            configured=False,
            healthy=False,
            authenticated=False,
            capabilities=(),
            rate_limit=None,
            diagnostics={"reason": "OPENROUTER_API_KEY not configured"},
            error=None,
        )
    health = await OpenRouterProvider(settings).health()
    return ConnectorReport(
        name="openrouter",
        configured=True,
        healthy=health.ok,
        authenticated=health.ok,
        capabilities=("model_discovery", "chat_completions"),
        rate_limit=None,
        diagnostics={"model_count": health.model_count, "latency_ms": health.latency_ms},
        error=health.error,
    )
