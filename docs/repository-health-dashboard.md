> **Document status:** Production reference  
> **Last reviewed:** 17 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

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
| AIMS | `/livez` | `/ops/health` |
| RAMS | `/livez` | Authenticated `/readiness` |
| MAST | Durable R2 heartbeat | Worker lag, failure streak and operator-control state |
| IRS | `/health.json` | Cloudflare Pages deployment and redirect-target audit events |
| Website | `/health.json` | Not applicable |

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
HIVE_UI_HEALTH_URL=https://hive.jonathan-harris.online/health
AIMS_HEALTH_URL=https://app.jonathan-harris.online/livez
AIMS_OPERATIONAL_HEALTH_URL=https://app.jonathan-harris.online/ops/health
RAMS_HEALTH_URL=https://mod.jonathan-harris.online/livez
RAMS_READINESS_URL=https://mod.jonathan-harris.online/readiness
RAMS_HEALTH_BEARER_TOKEN={{ secret.RMS_API_KEY }}
MAST_MONITOR_MODE=r2
MAST_STATE_R2_LANE=meta_system
MAST_STATE_OBJECT_KEY=state/mast/scheduler-state.json
MAST_STATE_HEALTHY_MAX_AGE_SECONDS=90
MAST_STATE_DOWN_MAX_AGE_SECONDS=300
IRS_HEALTH_URL=https://images.jonathan-harris.online/health.json
WEBSITE_HEALTH_URL=https://jonathan-harris.online/health.json
```

The RAMS token is sent only to the configured RAMS readiness URL. It is never returned in the health payload.

## Deployment order

1. Deploy HIVE and verify the backend tests and `/readyz`.
2. Set the exact production HIVE-UI, AIMS, RAMS, IRS and website URLs, plus the MAST R2 Worker-monitoring variables, in Koyeb.
3. Call `/v1/system/repo-health?force_refresh=true` with the HIVE admin bearer token.
4. Deploy HIVE-UI and verify the compact Ops cards and inspector payload.


## MAST Worker monitoring

Set `MAST_MONITOR_MODE=r2`, `MAST_STATE_OBJECT_KEY=state/mast/scheduler-state.json`, `R2_BUCKET_META_SYSTEM=metasystem` and valid scoped read credentials. HIVE reports current heartbeat as healthy, delayed heartbeat or repeated failures as degraded, and seriously stale/unreadable state as down. HTTP mode remains an explicit compatibility option only.
