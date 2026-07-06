# HIVE v1.26.12 CI Marker Sync Patch

Date: 22 June 2026

## Purpose

Synchronises backend test expectations and default version metadata with the v1.26.12 catalogue metadata release.

## CI failure addressed

GitHub Actions was running the v1.26.12 application code but several backend tests were still asserting the previous v1.26.11 build marker. The failure appeared as repeated assertions of `v1.26.12-catalogue-metadata` versus `v1.26.11-env-split`, plus `APP_VERSION` `1.26.12-production` versus `1.26.11-production`.

## Files updated

- `backend/app/core/config.py`
- `backend/app/core/version.py`
- `backend/tests/test_env_split.py`
- backend health/build marker tests
- `HIVE-PRODUCTION-SHARED.env`
- `HIVE-KOYEB-SECRETS-ONLY.env`

## Validation

- `PYTHONPATH=backend python -m pytest backend/tests -q`
- Result: `186 passed`
