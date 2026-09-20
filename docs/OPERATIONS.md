# HIVE production operations

**Status:** Paid Koyeb production service  
**Last reviewed:** 20 September 2026

Use `/livez` for process liveness, `/readyz` for public dependency readiness and authenticated `/v1/runtime/readiness` for detailed checks. MAST is monitored as a Worker through its durable R2 heartbeat, not through a public URL.

HIVE is also the ecosystem alert inbox. GitHub, Koyeb, Cloudflare Pages and runtime services post bounded redacted events to `/v1/ops/events`; HIVE-UI reads them from `/v1/system/ops-events`. See [`OPERATIONAL_ALERTING.md`](OPERATIONAL_ALERTING.md).

Routine operations: review readiness, repository health and operational events; verify the scoped R2 read credentials; retain release identifiers; and never weaken production gates to clear a dashboard warning. Roll back HIVE and HIVE-UI as a coordinated pair when an API contract changes.

## Repository Intelligence and controlled improvements

Repository uploads and monthly governed-repository refreshes run the Repository Intelligence pipeline automatically. The pipeline seeds Repository Memory, executes one Repository QA pass, feeds that exact QA evidence into Repository Council, refreshes Project DNA, and persists one snapshot-specific consolidated report.

The operator may then start `POST /v1/repositories/{repository_id}/improvements/run`. HIVE requires the latest Intelligence fingerprint to match the currently registered repository snapshot and refuses to run when there are no actionable findings. The improvement worker sends only bounded text context with credential-like values redacted and applies model-proposed changes to isolated candidate workspaces. The registered source snapshot is never modified by the improvement worker.

Repository improvements use a loop-first escalation protocol. HIVE attempts up to `REPOSITORY_IMPROVEMENT_MAX_LOOPS` (maximum 4) progressively governed coding models before any expert review. Each loop starts from the best validated candidate so far, records its model, QA score, blockers and promotion outcome, and cannot silently jump to a free or premium fallback. Only when all self-improvement loops miss the target does HIVE open an expert council review, hard-limited by `REPOSITORY_IMPROVEMENT_MAX_COUNCIL_RUNS` (maximum 2). A council result that remains below the target but falls within `REPOSITORY_IMPROVEMENT_NEAR_THRESHOLD_TOLERANCE` (default 5 percentage points) is accepted when there are no build, security, new-warning or score-regression blockers. This tolerance is council-only; ordinary loops must meet the full target.

Before publishing an artifact, HIVE runs its non-executing Repository QA checks against every candidate and refuses to promote a candidate that introduces a new QA warning, lowers the static QA score, fails build verification, or introduces a new secret-pattern warning. Successful jobs produce a changed-files ZIP and a full updated-repository ZIP. Production stores both artifacts in the configured repository R2 bucket and removes local expanded workspaces from ephemeral disk. `HIVE-IMPROVEMENT-REPORT.json` includes the complete loop/council outcome ledger and records whether the 5% rule was used.

HIVE static QA deliberately does not install repository dependencies or execute repository-owned build/test commands. Every completed improvement therefore carries a mandatory remaining-verification item requiring the repository's normal CI/release suite before deployment. The downloadable `HIVE-IMPROVEMENT-REPORT.json` records the source fingerprint, coding model, changed/deleted files, static validation result and remaining verification work.

No additional secret is introduced by this workflow. It uses the existing `OPENROUTER_API_KEY`, D1 configuration and repository R2 credentials.


## R2 and embeddings degraded-mode operations

R2 connector diagnostics are intentionally non-throwing. Invalid credentials, access denial, transient SDK/network errors and malformed list responses must surface as unhealthy/redacted diagnostics; they must not print configured access keys or secret keys. Use authenticated `/v1/connectors` and `/v1/runtime/readiness` to distinguish an optional degraded integration from a production-required readiness failure.

Workers AI embeddings are optional unless the deployment policy makes the dependent retrieval path mandatory. Timeout, connection failure, non-2xx, malformed JSON, unexpected response types and vector-count mismatch return a degraded result. Exception and provider text is redacted against the configured embeddings token before logging/return. Ordinary CI uses deterministic mocked-provider tests; live-provider smoke checks are separate and use deployment-managed credentials only.

For dependency maintenance, update `requirements.in`, regenerate `requirements.txt` with `python -m piptools compile --output-file=requirements.txt --strip-extras requirements.in`, then run `python scripts/verify_dependency_lock.py --compile`, the full test/static/security gates and the Docker smoke gate.
