> **Document status:** Production reference  
> **Last reviewed:** 29 September 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# Repository estate overview

HIVE exposes a read-only ecosystem repository/service health summary:

```http
GET /v1/system/repo-health
GET /v1/system/repo-health?force_refresh=true
```

The endpoint remains the backend authority for the **Repositories** overview in HIVE-UI. It is not an Operations-dashboard feed anymore. Repository health, snapshot freshness, Memory readiness and Intelligence readiness belong together on the repository estate surface so an operator sees one coherent status instead of the same repository data in two places.

The endpoint is read-only, uses only operator-configured targets, never accepts arbitrary probe URLs, redacts returned payloads and keeps a short bounded cache.

## UI ownership boundary

- **Repositories** owns governed repository registration/freshness and service-health overview.
- **Operations** owns HIVE runtime health, integration readiness, operational events, execution reviews, workflow planning and destructive database administration.
- HIVE-UI exposes no repository wake, repair, refresh-all, upload, reindex, setup, Intelligence-run or improvement controls.
- Scheduled repository refresh, QA, Council, Intelligence, CodeQL/Kilo repair and deployment verification remain backend/CI automation responsibilities.

`GET /v1/system/runtime-stats` intentionally excludes repository-manager counts so Operations cannot quietly recreate a second repository dashboard.

## Governed services

| Repository | Liveness | Operational/readiness |
|---|---|---|
| HIVE | Local process check | Local production-readiness report |
| HIVE-UI | Public Cloudflare Worker `/health` | Not applicable |
| AIMS-UI | Gateway liveness | Operator-console readiness |
| AIMS | Service liveness | Operational readiness |
| RAMS | Service liveness | Authenticated readiness |
| MAST | Durable scheduler heartbeat | Heartbeat freshness and bounded recent-result summary |
| IRS | Public service reachability | Not applicable |
| Website | Public site reachability | Not applicable |

## Status rules

- `healthy`: liveness passed and any operational check passed.
- `degraded`: liveness passed but operational readiness did not pass or was not configured.
- `down`: liveness failed or returned a non-success response.
- `not_configured`: no target was supplied.
- `standby` / `maintenance`: intentional lifecycle state supplied by authoritative lifecycle evidence.
- `starting`: expected bounded startup state.
- `disabled`: ecosystem monitoring is disabled globally.

## Security boundary

RAMS readiness credentials and other probe credentials are server-side only. They are never returned to HIVE-UI. The browser receives only the bounded health result required to render the repository overview.
