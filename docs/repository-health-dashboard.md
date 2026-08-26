> **Document status:** Production reference  
> **Last reviewed:** 26 August 2026  
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
| HIVE-UI | Public Cloudflare Worker `/health` | Not applicable |
| AIMS-UI | `https://chat.jonathan-harris.online/health` | Not applicable |
| AIMS | `/health` | `/ops/health` |
| RAMS | `/health` | Authenticated `/readiness` |
| MAST | Durable R2 scheduler heartbeat | Heartbeat freshness and bounded recent-result summary |
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
HIVE_UI_HEALTH_URL=https://hive.jonathan-harris.online/health
AIMS_UI_HEALTH_URL=https://chat.jonathan-harris.online/health
AIMS_HEALTH_URL=https://zeroth-kara-jonathanharris-3296ed37.koyeb.app/livez
AIMS_OPERATIONAL_HEALTH_URL=https://zeroth-kara-jonathanharris-3296ed37.koyeb.app/readyz
RAMS_HEALTH_URL=https://static-helaina-jonathanharris-6df5d241.koyeb.app/livez
RAMS_READINESS_URL=https://static-helaina-jonathanharris-6df5d241.koyeb.app/readiness
RAMS_HEALTH_BEARER_TOKEN={{ secret.RMS_API_KEY }}
MAST_MONITOR_MODE=r2
MAST_STATE_R2_LANE=meta_system
MAST_STATE_OBJECT_KEY=state/mast/scheduler-state.json
MAST_STATE_HEALTHY_MAX_AGE_SECONDS=90
MAST_STATE_DOWN_MAX_AGE_SECONDS=300
MAST_STATE_MAX_BYTES=1048576
IRS_HEALTH_URL=https://images.jonathan-harris.online/
WEBSITE_HEALTH_URL=https://jonathan-harris.online/
```

The RAMS token is sent only to the configured RAMS readiness URL. It is never returned in the health payload.

MAST runs as a Koyeb Worker and therefore has no public inbound health URL. In
`r2` mode HIVE reads the bounded scheduler state object from the configured
`meta_system` lane. Scoped S3 reads are preferred; the governed public R2 URL is
used as a read-only fallback when available. The worker is healthy while
`lastTickAt` remains within the healthy threshold, degraded while mildly stale,
and down only after the down threshold is exceeded.

## Deployment order

1. Deploy HIVE and verify the backend tests and `/readyz`.
2. Set the production HIVE-UI and AIMS-UI health URLs plus the MAST R2 heartbeat variables in Koyeb.
3. Call `/v1/system/repo-health?force_refresh=true` with the HIVE admin bearer token.
4. Deploy HIVE-UI and verify the compact Ops cards and inspector payload.
