from __future__ import annotations

import pytest

from app.ingestion.chunking import query_terms, score_chunk, split_text_into_chunks


def test_split_text_into_chunks_preserves_overlap_and_metadata() -> None:
    text = "Alpha paragraph about badgers. " * 20 + "Final paragraph about retrieval."

    chunks = split_text_into_chunks(text, max_chars=120, overlap_chars=20, max_chunks=20)

    assert len(chunks) > 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].char_start == 0
    assert chunks[0].char_end <= 120
    assert chunks[1].char_start < chunks[0].char_end
    assert chunks[0].content_sha256
    assert chunks[0].token_estimate > 0
    assert chunks[0].metadata["strategy"] == "paragraph_sentence_overlap_v1"


def test_split_text_rejects_tiny_chunk_size() -> None:
    with pytest.raises(ValueError):
        split_text_into_chunks("hello", max_chars=10)


def test_query_terms_and_score_chunk() -> None:
    terms = query_terms("Badger retrieval badger!")

    assert terms == ["badger", "retrieval"]
    assert score_chunk("This badger chunk has retrieval context.", "badger retrieval") > 0
    assert score_chunk("Unrelated fox note.", "badger retrieval") == 0
