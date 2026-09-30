#!/usr/bin/env python3
"""Validate a private Kilo trigger destination without displaying its value."""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import urlsplit


def valid_kilo_webhook_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and bool(parsed.path)
            and not parsed.fragment
            and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", host) is not None
            and (host == "hooks.kilosessions.ai" or host == "kilo.ai" or host.endswith(".kilo.ai"))
        )
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(0 if valid_kilo_webhook_url(os.environ.get("KILO_REPAIR_TRIGGER_URL", "")) else 1)
