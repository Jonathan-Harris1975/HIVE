from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.services.connectors import (
    ai_search_connector,
    github_connector,
    openrouter_connector,
    r2_connector,
)


async def all_connector_reports(settings: Settings) -> dict[str, Any]:
    reports = [
        await openrouter_connector.report(settings),
        await r2_connector.report(settings),
        await ai_search_connector.report(settings),
        await github_connector.report(settings),
    ]
    return {
        "connectors": [
            {
                "name": item.name,
                "configured": item.configured,
                "healthy": item.healthy,
                "authenticated": item.authenticated,
                "capabilities": item.capabilities,
                "rate_limit": item.rate_limit,
                "diagnostics": item.diagnostics,
                "error": item.error,
            }
            for item in reports
        ]
    }
