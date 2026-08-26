# HIVE production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 22 June 2026

Use `/livez` for process liveness, `/readyz` for public dependency readiness and authenticated `/v1/runtime/readiness` for detailed checks. MAST is monitored as a Worker through its durable R2 heartbeat, not through a public URL.

HIVE is also the ecosystem alert inbox. GitHub, Koyeb, Cloudflare Pages and runtime services post bounded redacted events to `/v1/ops/events`; HIVE-UI reads them from `/v1/system/ops-events`. See [`OPERATIONAL_ALERTING.md`](OPERATIONAL_ALERTING.md).

Routine operations: review readiness, repository health and operational events; verify the scoped R2 read credentials; retain release identifiers; and never weaken production gates to clear a dashboard warning. Roll back HIVE and HIVE-UI as a coordinated pair when an API contract changes.

## Repository Intelligence and controlled improvements

Repository uploads and monthly governed-repository refreshes run the Repository Intelligence pipeline automatically. The pipeline seeds Repository Memory, executes one Repository QA pass, feeds that exact QA evidence into Repository Council, refreshes Project DNA, and persists one snapshot-specific consolidated report.

The operator may then start `POST /v1/repositories/{repository_id}/improvements/run`. HIVE requires the latest Intelligence fingerprint to match the currently registered repository snapshot and refuses to run when there are no actionable findings. The improvement worker uses the configured coding model through OpenRouter, sends only bounded text context with credential-like values redacted, and applies model-proposed changes to an isolated working copy. The registered source snapshot is never modified by the improvement worker.

Before publishing an artifact, HIVE runs its non-executing Repository QA checks against the isolated copy and refuses to publish if the generated copy introduces a new QA warning, lowers the static QA score, fails Python compilation, or triggers the secret-pattern check. Successful jobs produce a changed-files ZIP and a full updated-repository ZIP. Production stores both artifacts in the configured repository R2 bucket and removes the local expanded copy from ephemeral disk.

HIVE static QA deliberately does not install repository dependencies or execute repository-owned build/test commands. Every completed improvement therefore carries a mandatory remaining-verification item requiring the repository's normal CI/release suite before deployment. The downloadable `HIVE-IMPROVEMENT-REPORT.json` records the source fingerprint, coding model, changed/deleted files, static validation result and remaining verification work.

No additional secret is introduced by this workflow. It uses the existing `OPENROUTER_API_KEY`, D1 configuration and repository R2 credentials.
