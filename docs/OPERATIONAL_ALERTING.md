# HIVE operational alerting

**Status:** Production-ready contract  
**Last reviewed:** 17 June 2026

HIVE is the central, redacted operational-event inbox for the ecosystem. It accepts trusted events from GitHub Actions, Koyeb deployment watchers, Cloudflare Pages watchers and runtime services, then exposes them to the authenticated HIVE-UI Ops page. This supplements provider email and does not depend on an email being noticed.

## Production configuration

```env
OPS_EVENT_INGEST_ENABLED=true
OPS_EVENT_INGEST_TOKEN={{ secret.OPS_EVENT_INGEST_TOKEN }}
OPS_EVENT_MEMORY_LIMIT=200
```

The token must be unique, at least 32 characters and separate from `ADMIN_BEARER_TOKEN`. With D1 enabled, events are persisted in the `ops_events` lane. Without D1, the bounded in-memory queue remains available but is cleared by redeployment.

## Endpoints

| Endpoint | Authentication | Purpose |
|---|---|---|
| `POST /v1/ops/events` | Dedicated event token | Accept a bounded operational event |
| `GET /v1/system/ops-events` | HIVE admin bearer | List recent redacted events for HIVE-UI |

Payloads are size-bounded and keys resembling credentials, tokens or passwords are redacted before storage. Event senders must never include logs, prompts, request bodies or secret values.

## Repository secrets

Add these GitHub Actions secrets to every governed repository:

- `OPS_ALERT_WEBHOOK_URL`, set to the production HIVE API plus `/v1/ops/events`
- `OPS_ALERT_WEBHOOK_TOKEN`, matching the HIVE ingest token

Koyeb-backed repositories also require `KOYEB_TOKEN` and `KOYEB_SERVICE` for the post-CI deployment watcher. `KOYEB_SERVICE` should be the exact `app/service` reference or service identifier accepted by the Koyeb CLI.

## Failure behaviour

Notification jobs are `continue-on-error`: alert delivery can never turn a successful build red or hide the original failing job. Watchers use provider APIs/CLIs, bounded polling and stable event IDs so retries do not create a confetti storm of duplicates.

## Operator response

1. Open HIVE-UI `/ops` and inspect the event card.
2. Follow the provider run/deployment link.
3. Confirm the affected production service and release identifier.
4. Roll back or correct the configuration without disabling readiness gates.
5. Retain the event and provider evidence with the release record.
