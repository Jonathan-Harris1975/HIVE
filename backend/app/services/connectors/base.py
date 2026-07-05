from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Phase 10 - Connector Framework.
#
# A connector is a lighter-weight cousin of a Phase 4 provider: it wraps an
# existing infrastructure integration (OpenRouter, Cloudflare R2, Cloudflare
# AI Search, GitHub) with one uniform diagnostic shape, without changing how
# that integration is actually used elsewhere in the codebase. Each
# connector module in this package wraps an existing client/service rather
# than reimplementing it.


@dataclass(frozen=True)
class ConnectorReport:
    name: str
    configured: bool
    healthy: bool
    authenticated: bool
    capabilities: tuple[str, ...]
    rate_limit: dict[str, Any] | None
    diagnostics: dict[str, Any]
    error: str | None
