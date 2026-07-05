from __future__ import annotations

import pytest

from app.services import bucket_manager as bm


def test_accessible_and_hidden_buckets_do_not_overlap():
    assert set(bm.ACCESSIBLE_BUCKETS) & set(bm.HIDDEN_BUCKETS) == set()


def test_is_accessible_true_for_known_bucket():
    assert bm.is_accessible("hive-repositories") is True
    assert bm.is_accessible("metasystem") is False


def test_is_hidden_true_for_hidden_bucket():
    assert bm.is_hidden("raw-text") is True
    assert bm.is_hidden("hive") is False


def test_assert_accessible_raises_for_hidden_bucket():
    with pytest.raises(bm.BucketAccessError):
        bm.assert_accessible("podcast-meta")


def test_assert_accessible_raises_for_unknown_bucket():
    with pytest.raises(bm.BucketAccessError):
        bm.assert_accessible("not-a-real-bucket")


def test_assert_accessible_passes_for_known_bucket():
    bm.assert_accessible("blog")  # should not raise


def test_filter_hidden_strips_hidden_bucket_names():
    names = ["hive", "metasystem", "blog", "raw-text"]
    assert bm.filter_hidden(names) == ["hive", "blog"]


def test_list_accessible_buckets_excludes_all_hidden_names():
    accessible = bm.list_accessible_buckets()
    assert not any(name in bm.HIDDEN_BUCKETS for name in accessible)
