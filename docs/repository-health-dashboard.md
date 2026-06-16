# Repository and Service Health Dashboard

HIVE exposes an authenticated ecosystem health summary for HIVE-UI:

```http
GET /v1/system/repo-health
GET /v1/system/repo-health?force_refresh=true
```

The endpoint is read-only, uses only operator-configured URLs, never accepts an arbitrary probe target, redacts response payloads, and caches results for a short period.

## Repositories and services

| Repository | Liveness | Operational/readiness |
|---|---|---|
| HIVE | Local process check | Local production-readiness report |
| HIVE-UI | Configured public Pages URL | Not applicable |
| AIMS | `/health` | `/ops/health` |
| RAMS | `/health` | Authenticated `/readiness` |
| MAST | `/health` | `/status` |
| IRS | Public root reachability | Not applicable |
| Website | Public root reachability | Not applicable |

AIMS and RAMS deliberately receive deeper checks because a background API can be alive while credentials, storage, repositories, queues, or downstream providers are degraded.

## Status rules

- `healthy`: liveness passed and any operational check passed.
- `degraded`: liveness passed but operational readiness did not pass or was not configured.
- `down`: liveness failed or returned a non-success response.
- `not_configured`: no target URL was supplied.
- `disabled`: ecosystem monitoring is disabled globally.

## Environment variables

```text
REPO_HEALTH_ENABLED=true
REPO_HEALTH_TIMEOUT_SECONDS=6
REPO_HEALTH_CACHE_SECONDS=30
HIVE_UI_HEALTH_URL=https://your-hive-ui-domain.example
AIMS_HEALTH_URL=https://app.jonathan-harris.online/health
AIMS_OPERATIONAL_HEALTH_URL=https://app.jonathan-harris.online/ops/health
RAMS_HEALTH_URL=https://mod.jonathan-harris.online/health
RAMS_READINESS_URL=https://mod.jonathan-harris.online/readiness
RAMS_HEALTH_BEARER_TOKEN={{ secret.RMS_API_KEY }}
MAST_HEALTH_URL=https://your-mast-domain.example/health
MAST_STATUS_URL=https://your-mast-domain.example/status
IRS_HEALTH_URL=https://images.jonathan-harris.online/
WEBSITE_HEALTH_URL=https://jonathan-harris.online/
```

The RAMS token is sent only to the configured RAMS readiness URL. It is never returned in the health payload.

## Deployment order

1. Deploy HIVE and verify the backend tests and `/readyz`.
2. Set the exact production HIVE-UI and MAST URLs in Koyeb.
3. Call `/v1/system/repo-health?force_refresh=true` with the HIVE admin bearer token.
4. Deploy HIVE-UI and verify the compact Ops cards and inspector payload.
