from __future__ import annotations

from app.core.config import Settings
from app.services.connectors.base import ConnectorReport
from app.storage.ai_search import AiSearchClient


async def report(settings: Settings) -> ConnectorReport:
    client = AiSearchClient(settings)
    diagnostics = await client.diagnostics()
    return ConnectorReport(
        name="cloudflare_ai_search",
        configured=client.enabled,
        healthy=bool(diagnostics.get("ok")),
        authenticated=client.enabled,
        capabilities=("semantic_search",) if client.enabled else (),
        rate_limit=None,
        diagnostics=diagnostics,
        error=None if client.enabled else diagnostics.get("reason"),
    )
