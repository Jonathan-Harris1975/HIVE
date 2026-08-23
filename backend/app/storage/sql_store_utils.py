from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from app.core.config import Settings

def _strip_nul_text(value: str) -> str:
    return value.replace("\x00", "\ufffd")


def _strip_nul(value: Any) -> Any:
    if isinstance(value, str):
        return _strip_nul_text(value)
    if isinstance(value, dict):
        return {_strip_nul_text(str(key)): _strip_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strip_nul(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_strip_nul(item) for item in value)
    return value


def _json_dumps_safe(value: Any) -> str:
    return json.dumps(_strip_nul(value), ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DatabaseUrlBuilder:
    @staticmethod
    def from_parts(settings: Settings) -> str:
        if settings.database_url:
            return settings.database_url
        if settings.database_host and settings.database_user and settings.database_name:
            user = quote_plus(settings.database_user)
            password = quote_plus(settings.database_password)
            auth = f"{user}:{password}" if password else user
            port = f":{settings.database_port}" if settings.database_port else ""
            ssl = f"?sslmode={quote_plus(settings.database_sslmode)}" if settings.database_sslmode else ""
            return f"postgresql://{auth}@{settings.database_host}{port}/{settings.database_name}{ssl}"
        if settings.is_dev:
            return "sqlite:///./local-data/hive.sqlite3"
        return ""


def _default_conversation_title(message: str, *, max_length: int = 72) -> str | None:
    clean = " ".join((message or "").split()).strip()
    if not clean:
        return None
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 1].rstrip() + "…"


def _json_or_none(value: Any) -> Any:
    if value in {None, ""}:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _query_terms(query: str, *, max_terms: int = 8) -> list[str]:
    terms = [term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{1,}", query or "")]
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique.append(term)
        if len(unique) >= max_terms:
            break
    return unique


def _score_chunk(content: str, query: str) -> float:
    terms = _query_terms(query)
    if not terms:
        return 0.0
    lowered = content.lower()
    score = 0.0
    query_lowered = (query or "").strip().lower()
    if query_lowered and query_lowered in lowered:
        score += 10.0
    for term in terms:
        count = lowered.count(term)
        if count:
            score += 2.0 * count
            if term in lowered[:240]:
                score += 0.5
    return round(score, 3)


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
