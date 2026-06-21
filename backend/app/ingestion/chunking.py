from __future__ import annotations

import hashlib
from app.core.text_safety import strip_nul_text
import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class TextChunk:
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    token_estimate: int
    content_sha256: str
    metadata: dict[str, Any]


def split_text_into_chunks(
    text: str,
    *,
    max_chars: int = 4000,
    overlap_chars: int = 400,
    max_chunks: int = 500,
) -> list[TextChunk]:
    """Split text into durable retrieval chunks.

    The splitter is deliberately simple and deterministic. It favours paragraph,
    line and sentence boundaries near the end of the target window while keeping
    a small overlap so follow-up retrieval does not lose context at chunk seams.
    """

    if max_chars < 80:
        raise ValueError("max_chars must be at least 80")
    if max_chunks < 1:
        raise ValueError("max_chunks must be at least 1")

    normalised = strip_nul_text(text).replace("\r\n", "\n").replace("\r", "\n")
    if not normalised.strip():
        return []

    overlap = max(0, min(int(overlap_chars), max_chars // 2))
    chunks: list[TextChunk] = []
    start = 0
    text_len = len(normalised)

    while start < text_len and len(chunks) < max_chunks:
        hard_end = min(text_len, start + max_chars)
        end = _best_break(normalised, start, hard_end, max_chars=max_chars)
        if end <= start:
            end = hard_end

        raw = normalised[start:end]
        leading_trim = len(raw) - len(raw.lstrip())
        trailing_trim = len(raw.rstrip())
        content = raw.strip()

        if content:
            char_start = start + leading_trim
            char_end = start + trailing_trim
            chunks.append(
                TextChunk(
                    chunk_index=len(chunks),
                    content=content,
                    char_start=char_start,
                    char_end=char_end,
                    token_estimate=estimate_tokens(content),
                    content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    metadata={
                        "strategy": "paragraph_sentence_overlap_v1",
                        "max_chars": max_chars,
                        "overlap_chars": overlap,
                    },
                )
            )

        if end >= text_len:
            break
        start = max(end - overlap, start + 1)

    return chunks


def chunks_to_dicts(chunks: list[TextChunk]) -> list[dict[str, Any]]:
    return [asdict(chunk) for chunk in chunks]


def estimate_tokens(text: str) -> int:
    # Cheap approximation for diagnostics/cost planning, not billing.
    return max(1, round(len(text) / 4)) if text.strip() else 0


def query_terms(query: str, *, max_terms: int = 8) -> list[str]:
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


def score_chunk(content: str, query: str) -> float:
    terms = query_terms(query)
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


def _best_break(text: str, start: int, hard_end: int, *, max_chars: int) -> int:
    if hard_end >= len(text):
        return len(text)

    search_start = max(start, hard_end - max(max_chars // 3, 80))
    window = text[search_start:hard_end]
    for separator in ("\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " "):
        pos = window.rfind(separator)
        if pos >= 0:
            return search_start + pos + len(separator)
    return hard_end
