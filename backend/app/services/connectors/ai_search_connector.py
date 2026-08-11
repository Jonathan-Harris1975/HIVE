from __future__ import annotations

from app.core.config import Settings
from app.services.connectors.base import ConnectorReport
from app.storage.ai_search import AiSearchClient


async def report(settings: Settings) -> ConnectorReport:
    client = AiSearchClient(settings)
    diagnostics = await client.diagnostics()
    healthy = bool(diagnostics.get("ok"))
    return ConnectorReport(
        name="cloudflare_ai_search",
        configured=client.enabled,
        healthy=healthy,
        authenticated=client.enabled and healthy,
        capabilities=("semantic_search", "repository_memory") if client.enabled else (),
        rate_limit=None,
        diagnostics=diagnostics,
        error=None if healthy else str(diagnostics.get("reason") or "AI Search health check failed."),
    )
