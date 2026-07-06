# HIVE v1.26.8 CI preflight RAMS auth patch

Date: 22 June 2026

## Fresh CI failure

The fresh HIVE CI run failed in the backend job during the Production configuration preflight step.

Backend tests passed:

```text
177 passed in 5.27s
```

Docker/container build completed successfully.

## Failing check

The preflight report failed only on `rams_readiness_auth`:

```text
RAMS_READINESS_URL is configured but no RAMS_READINESS_BEARER_TOKEN/RAMS_HEALTH_BEARER_TOKEN/RMS_API_KEY is available.
```

The application now correctly requires a RAMS readiness bearer token whenever RAMS readiness is configured in production. The CI workflow environment had not been updated to include the mock CI RAMS token.

## Fix

Updated `.github/workflows/ci.yml` so the Production configuration preflight includes explicit RAMS health/readiness URLs and mock CI bearer tokens:

```yaml
RAMS_HEALTH_URL: https://mod.jonathan-harris.online/livez
RAMS_READINESS_URL: https://mod.jonathan-harris.online/readiness
RAMS_HEALTH_BEARER_TOKEN: ci-rams-token-with-sufficient-length
RAMS_READINESS_BEARER_TOKEN: ci-rams-token-with-sufficient-length
```

## Local validation

The same production preflight command now returns:

```text
ready: true
error_count: 0
warning_count: 0
```

No runtime rollback is required.
