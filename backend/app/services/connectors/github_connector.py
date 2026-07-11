from __future__ import annotations

import logging

import httpx

from app.core.config import Settings
from app.services.connectors.base import ConnectorReport

logger = logging.getLogger("uvicorn.error.hive.github_connector")


async def report(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> ConnectorReport:
    if not settings.github_token:
        return ConnectorReport(
            name="github",
            configured=False,
            healthy=False,
            authenticated=False,
            capabilities=(),
            rate_limit=None,
            diagnostics={"reason": "GITHUB_TOKEN not configured"},
            error=None,
        )

    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            rate_response = await client.get("https://api.github.com/rate_limit", headers=headers)
            rate_response.raise_for_status()
            rate_payload = rate_response.json()

            repo_diagnostics: dict[str, object] = {}
            if settings.github_repository:
                repo_response = await client.get(
                    f"https://api.github.com/repos/{settings.github_repository}", headers=headers
                )
                if repo_response.status_code == 200:
                    repo_data = repo_response.json()
                    repo_diagnostics = {
                        "full_name": repo_data.get("full_name"),
                        "default_branch": repo_data.get("default_branch"),
                        "private": repo_data.get("private"),
                    }

        core_limit = (rate_payload.get("resources") or {}).get("core") or {}
        return ConnectorReport(
            name="github",
            configured=True,
            healthy=True,
            authenticated=True,
            capabilities=("repo_metadata", "rate_limit"),
            rate_limit={
                "limit": core_limit.get("limit"),
                "remaining": core_limit.get("remaining"),
                "reset": core_limit.get("reset"),
            },
            diagnostics=repo_diagnostics,
            error=None,
        )
    except httpx.HTTPError as error:
        logger.warning("GitHub connector health check failed error=%s", error)
        return ConnectorReport(
            name="github",
            configured=True,
            healthy=False,
            authenticated=False,
            capabilities=(),
            rate_limit=None,
            diagnostics={},
            error=str(error),
        )
