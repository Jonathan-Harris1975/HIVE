# HIVE Ecosystem Production Manager

**Status:** Production-controlled  
**Authority:** HIVE is the Ecosystem Production Manager for the eight governed repositories.

## Responsibility model

HIVE is accountable for the current ecosystem production state and exposes the deterministic decision through:

```http
GET /v1/system/production-manager
GET /v1/system/production-manager?force_refresh=true
```

The manager consumes the existing eight-repository health/readiness snapshot. It does not replace repository CI, security or deployment gates.

| Responsibility | Authority |
| --- | --- |
| Ecosystem production state | HIVE |
| Repository readiness certification | Deterministic CI, security, deployment and runtime gates |
| Bounded implementation/remediation | Kilo |
| High-risk technical/architectural escalation | CTO |
| Owner-only decisions | owner |

Kilo may prepare or apply bounded repairs through the governed repository workflow, but Kilo does not certify those repairs. A repair returns through the repository's independent CI/security/deployment path before HIVE can regard the repository as production-ready.

## Production states

HIVE publishes one of three ecosystem states:

- **GREEN**: all eight governed repositories satisfy current production checks. Ecosystem release decision is `ALLOW`.
- **DEGRADED**: at least one repository is starting, degraded, not configured or missing from the snapshot. Ecosystem release decision is `HOLD`.
- **BLOCKED**: at least one repository is down, blocked, unavailable or failed. Ecosystem release decision is `BLOCK`.

Intentional `standby` and `maintenance` lifecycle states remain GREEN when the underlying lifecycle ledger says the state is deliberate.

The manager fails closed. Disabled repository-health monitoring or a governed repository missing from the current snapshot cannot produce GREEN.

## Governed repositories

The canonical estate remains:

1. HIVE
2. HIVE-UI
3. AIMS
4. AIMS-UI
5. RAMS
6. MAST
7. IRS
8. Website (`Jonathan-Harris1975/jonathan-harris-website`)

Each repository carries `.github/production-governance.json` declaring the same authority boundary locally.

## Owner-only boundary

Automation must stop and escalate to the owner for:

- secrets or credential changes;
- irreversible production actions;
- security-policy exceptions;
- legal or commercial decisions;
- destructive data operations.

Routine failures should not reach the owner merely because automation encountered them. HIVE holds or blocks production state, Kilo performs bounded remediation, deterministic checks re-certify the result, and CTO is used only when the issue crosses the high-risk technical boundary.

## Existing evidence sources

The Production Manager deliberately reuses the controls already in place:

- HIVE repository-health probes and production readiness;
- repository CI and security gates;
- exact-SHA Koyeb/Cloudflare deployment verification;
- MAST ecosystem smoke;
- HIVE operational-event ingestion;
- Repository QA/Council/Intelligence evidence.

This avoids a second source of truth. HIVE is the production authority; the underlying checks remain the evidence.
