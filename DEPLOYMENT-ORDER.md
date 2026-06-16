# HIVE ecosystem production deployment order

**Release status:** Ready for controlled deployment and live verification  
**Prepared:** 16 June 2026

## 1. MAST configuration prerequisite

Create or confirm the MAST R2 scheduler-state secrets and variables from `MAST/MAST-KOYEB-PRODUCTION-ENV-PATCH.txt`. In the Koyeb app `overall-frances`, service `mast-1`, copy the exact public domain from the Domains panel. The resource reference is not itself a public URL.

Prepare these HIVE values but do not guess the hostname:

```text
MAST_HEALTH_URL=https://<exact-mast-domain>/health
MAST_STATUS_URL=https://<exact-mast-domain>/status
```

## 2. Static health producers

Deploy and verify:

1. IRS, then probe `https://images.jonathan-harris.online/health.json` and sample redirects.
2. Website, then probe `https://jonathan-harris.online/health.json` and allow the strict post-deploy live gate to finish.
3. HIVE-UI, then probe `https://hive.jonathan-harris.online/health` and test signed login.

## 3. Background APIs

Deploy in this order:

1. AIMS: `/livez`, `/readyz`, then one low-risk dry run.
2. RAMS: `/livez`, authenticated `/readyz`, then one dry-run audit.
3. MAST: `/livez`, `/readyz`, `/status`, then authenticated `/status/details`.

Keep MAST scheduling disabled during upstream smoke tests if there is any risk of duplicate work. Enable scheduling only after durable R2 state is confirmed.

## 4. HIVE backend

Deploy HIVE last so its health aggregator sees the final endpoints. Apply `HIVE/HIVE-REPO-HEALTH-KOYEB-ENV-PATCH.txt`, replacing the two MAST placeholders with the exact domain.

Verify:

- `/livez`
- `/readyz`
- authenticated `/v1/runtime/readiness`
- authenticated `/v1/files/r2-lanes`
- authenticated `/v1/models`
- authenticated `/v1/system/repo-health?force_refresh=true`

## 5. Operator acceptance

Open HIVE-UI `/ops` and confirm all seven repositories appear. Repository and operational health cards should display two per row on suitable widths, remain compact, and expose detail through the inspector. A degraded dependency should affect only its own card rather than breaking the dashboard.
