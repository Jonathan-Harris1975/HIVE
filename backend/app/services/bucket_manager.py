from __future__ import annotations

# Phase 9 - Bucket Manager.
#
# Explicit accessible/hidden bucket registry per the programme spec. Hidden
# buckets must never appear in normal UI or repository workflows — anything
# that lists or resolves a bucket name by user input should call
# `is_accessible()` (or `assert_accessible()`) first.

ACCESSIBLE_BUCKETS: tuple[str, ...] = (
    "audits",
    "blog",
    "blog-images",
    "podcast-rss-feeds",
    "podcastart",
    "brand-assets",
    "hive",
    "rss-feed-blog",
    "hive-repositories",
    "rss-feeds",
    "transcripts",
    "hive-skills",
    "podcast",
)

HIDDEN_BUCKETS: tuple[str, ...] = (
    "metasystem",
    "podcast-chunks",
    "podcast-intro-outro-music",
    "podcast-merged",
    "podcast-meta",
    "raw-text",
    "edited",
)


class BucketAccessError(PermissionError):
    pass


def is_accessible(bucket_name: str) -> bool:
    return bucket_name in ACCESSIBLE_BUCKETS


def is_hidden(bucket_name: str) -> bool:
    return bucket_name in HIDDEN_BUCKETS


def assert_accessible(bucket_name: str) -> None:
    """Raise BucketAccessError for hidden or unknown buckets. Callers that
    resolve a bucket name from user/UI input should call this before use."""
    if bucket_name in HIDDEN_BUCKETS:
        raise BucketAccessError(f"Bucket {bucket_name!r} is hidden and cannot be accessed via this path.")
    if bucket_name not in ACCESSIBLE_BUCKETS:
        raise BucketAccessError(f"Bucket {bucket_name!r} is not a recognised HIVE bucket.")


def list_accessible_buckets() -> list[str]:
    return list(ACCESSIBLE_BUCKETS)


def filter_hidden(bucket_names: list[str]) -> list[str]:
    """Strip any hidden bucket names out of a list before it is ever
    returned to a UI, workflow, or repository operation."""
    return [name for name in bucket_names if name not in HIDDEN_BUCKETS]
