# HIVE production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 22 June 2026

Use `/livez` for process liveness, `/readyz` for public dependency readiness and authenticated `/v1/runtime/readiness` for detailed checks. MAST is monitored as a Worker through its durable R2 heartbeat, not through a public URL.

HIVE is also the ecosystem alert inbox. GitHub, Koyeb, Cloudflare Pages and runtime services post bounded redacted events to `/v1/ops/events`; HIVE-UI reads them from `/v1/system/ops-events`. See [`OPERATIONAL_ALERTING.md`](OPERATIONAL_ALERTING.md).

Routine operations: review readiness, repository health and operational events; verify the scoped R2 read credentials; retain release identifiers; and never weaken production gates to clear a dashboard warning. Roll back HIVE and HIVE-UI as a coordinated pair when an API contract changes.
