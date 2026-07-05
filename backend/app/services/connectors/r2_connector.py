from __future__ import annotations

from app.core.config import Settings
from app.services.connectors.base import ConnectorReport
from app.storage.r2 import R2Storage


async def report(settings: Settings) -> ConnectorReport:
    storage = R2Storage(settings)
    if not storage.enabled:
        return ConnectorReport(
            name="cloudflare_r2",
            configured=False,
            healthy=False,
            authenticated=False,
            capabilities=(),
            rate_limit=None,
            diagnostics={"reason": "R2 credentials not fully configured"},
            error=None,
        )
    try:
        keys = storage.list_keys(limit=1)
        capabilities = ["read"]
        capabilities.append("write" if storage.write_enabled else "read_only")
        if storage.read_enabled:
            capabilities.append("multi_bucket_read")
        return ConnectorReport(
            name="cloudflare_r2",
            configured=True,
            healthy=True,
            authenticated=True,
            capabilities=tuple(capabilities),
            rate_limit=None,
            diagnostics={"bucket": settings.cf_r2_bucket, "sample_key_count": len(keys)},
            error=None,
        )
    except Exception as error:  # noqa: BLE001 - connector diagnostics must never raise
        return ConnectorReport(
            name="cloudflare_r2",
            configured=True,
            healthy=False,
            authenticated=False,
            capabilities=(),
            rate_limit=None,
            diagnostics={"bucket": settings.cf_r2_bucket},
            error=str(error),
        )
