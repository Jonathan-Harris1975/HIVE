# HIVE v1.26.13 R2 Repositories Bucket Default Fix

Date: 5 July 2026

## Purpose

Resolves a CI regression in which four backend tests failed production
configuration validation with:

```
app.core.production.ProductionConfigurationError: HIVE production
configuration is invalid: r2_multi_bucket_read: R2 multi-bucket read access
is enabled but neither shared write credentials nor read-only credentials
are complete.
```

## Root cause

`backend/app/core/config.py` shipped hard-coded, account-specific defaults
for the (currently unused) Repository Manager R2 lane:

```python
r2_bucket_repositories: str = Field("hive-repositories", ...)
r2_public_base_url_repositories: str = Field(
    "https://pub-c48ec7e8f0b64be39259e09db7de0f94.r2.dev", ...
)
```

Every other ecosystem R2 lane (`audits`, `blog`, `meta`, `hive_skills`,
etc.) defaults to an empty string and is only populated when an operator
sets the corresponding environment variable. Because these two fields
defaulted to non-empty values instead, `Settings.r2_ecosystem_lanes`
always reported the `repositories` lane as "configured" even when nobody
had set `R2_BUCKET_REPOSITORIES` or `R2_PUBLIC_BASE_URL_REPOSITORIES`.

`app.core.production.build_readiness_report` treats any configured,
non-primary bucket as a signal that scoped multi-bucket R2 read access is
required in production (`non_primary_buckets_configured`). With the
`repositories` lane always "configured" by default, every production
readiness check now required complete R2 read credentials even in test
configurations that never touched R2 at all — including the minimal
hardened configuration used throughout
`backend/tests/test_production_readiness.py`.

The `repositories` lane is not read or written anywhere in the codebase
today (Repository Manager currently only uses local temp storage; R2
upload of extracted archives is tracked as future work in
`docs/releases/v1.27-repository-manager.md`), so the bucket name and
public URL were never meant to be baked-in defaults — they were meant to
be operator-supplied environment values, exactly as documented in
`.env.example`.

## Fix

- `backend/app/core/config.py`: `r2_bucket_repositories` and
  `r2_public_base_url_repositories` now default to `""`, consistent with
  every other R2 ecosystem lane. Operators who want the `repositories`
  lane to appear in `r2_ecosystem_lanes` continue to set
  `R2_BUCKET_REPOSITORIES` / `R2_PUBLIC_BASE_URL_REPOSITORIES` exactly as
  `.env.example` and the Koyeb env templates already document — no
  behavioural change for any environment that sets these variables.

Production security is unchanged: the `r2_multi_bucket_read` check still
enforces complete read credentials whenever a non-primary bucket, an
explicit `R2_REQUIRED_READ_LANES` list, or `PRODUCTION_REQUIRE_R2` genuinely
requires it. This fix only removes a false-positive trigger caused by an
unconfigured lane appearing "configured" by default.

## Files updated

- `backend/app/core/config.py`

## Validation

- `PYTHONPATH=backend python -m pytest backend/tests -q`
