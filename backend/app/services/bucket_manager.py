from __future__ import annotations

# Phase 9 - Bucket Manager.
#
# Hidden buckets must never appear in normal UI or repository workflows.
# `HIDDEN_BUCKETS` is the live registry consumed by app/api/buckets.py to
# filter listings. The rest of this module's original guard helpers
# (is_accessible/assert_accessible/list_accessible_buckets/filter_hidden)
# were superseded by the live R2 lane registry in app.core.config and had
# zero production callers, so they were removed during the 2026-07
# production-readiness audit. See app/api/buckets.py for the current
# source of truth on accessible bucket lanes.

HIDDEN_BUCKETS: tuple[str, ...] = (
    "metasystem",
    "podcast-chunks",
    "podcast-intro-outro-music",
    "podcast-merged",
    "podcast-meta",
    "raw-text",
    "edited",
)


def is_hidden(bucket_name: str) -> bool:
    return bucket_name in HIDDEN_BUCKETS
