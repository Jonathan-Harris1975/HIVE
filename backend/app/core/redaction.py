from __future__ import annotations

from typing import Any


_REDACTED = "[redacted]"


def redact_text(value: object, *secrets: str) -> str:
    """Return text with configured credential values removed.

    This deliberately redacts exact configured values rather than trying to
    guess every possible secret syntax. Callers know which credentials were
    used for a provider request, so exact-value redaction is both predictable
    and resistant to false negatives in exception/log paths.
    """

    text = str(value)
    candidates = {secret for secret in secrets if isinstance(secret, str) and secret}
    for secret in sorted(candidates, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    return text


def redact_payload(value: Any, *secrets: str) -> Any:
    """Recursively redact configured secrets from provider response payloads."""

    if isinstance(value, str):
        return redact_text(value, *secrets)
    if isinstance(value, list):
        return [redact_payload(item, *secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_payload(item, *secrets) for item in value)
    if isinstance(value, dict):
        return {
            redact_text(key, *secrets) if isinstance(key, str) else key: redact_payload(item, *secrets)
            for key, item in value.items()
        }
    return value
