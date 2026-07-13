from __future__ import annotations

from app.services import bucket_manager as bm


def test_is_hidden_true_for_hidden_bucket():
    assert bm.is_hidden("raw-text") is True
    assert bm.is_hidden("hive") is False


def test_hidden_buckets_registry_matches_known_hidden_lanes():
    assert set(bm.HIDDEN_BUCKETS) == {
        "metasystem",
        "podcast-chunks",
        "podcast-intro-outro-music",
        "podcast-merged",
        "podcast-meta",
        "raw-text",
        "edited",
    }
