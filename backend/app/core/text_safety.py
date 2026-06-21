from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

NUL_REPLACEMENT = "\ufffd"


def strip_nul_text(value: str) -> str:
    """Return text that is safe for PostgreSQL text/json fields.

    PostgreSQL rejects the literal NUL codepoint (0x00) in text-like values. R2
    objects and extracted ZIP members can legally contain those bytes, so every
    durable SQL write path normalises them before binding query parameters.
    """

    return value.replace("\x00", NUL_REPLACEMENT)


def strip_nul_data(value: Any) -> Any:
    """Recursively remove NUL codepoints while preserving the data shape."""

    if isinstance(value, str):
        return strip_nul_text(value)
    if is_dataclass(value) and not isinstance(value, type):
        return strip_nul_data(asdict(value))
    if isinstance(value, dict):
        return {strip_nul_text(str(key)): strip_nul_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [strip_nul_data(item) for item in value]
    if isinstance(value, set):
        return [strip_nul_data(item) for item in sorted(value, key=str)]
    return value


def sql_safe_json_dumps(value: Any) -> str:
    """JSON serialise data after removing PostgreSQL-hostile NUL codepoints."""

    return json.dumps(
        strip_nul_data(value),
        ensure_ascii=False,
        default=lambda item: strip_nul_text(str(item)),
    )
