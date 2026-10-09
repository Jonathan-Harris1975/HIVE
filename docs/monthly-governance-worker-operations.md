# Monthly governance worker: deployment and operations

The monthly governance HTTP POST is **not** a background queue. The independent
worker must run in a persistent, scheduled process with the same HIVE backend
code and production configuration. Merging the worker does not deploy it.

## One-time production setup

1. Provision a scheduled container/job using the HIVE backend image and Python
   application dependencies. Set its working directory to the backend package
   root so `python -m app.monthly_governance_worker` resolves correctly.
2. Inject production configuration through your deployment secret manager.
   Required: `D1_ENABLED=true`, `D1_ACCOUNT_ID`, `D1_DATABASE_ID`,
   `D1_API_KEY`, `CF_R2_ACCOUNT_ID`, `CF_R2_ACCESS_KEY_ID`,
   `CF_R2_SECRET_ACCESS_KEY`, `R2_BUCKET_AUDITS`, `AIMS_BASE_URL`,
   `AIMS_API_KEY`, `RAMS_BASE_URL`, and `RAMS_API_KEY`.
   Confirm the actual R2 lane and bucket configuration used by the monthly
   review, not just the audit bucket. Never commit secret values.
3. Verify the D1 schema contains `hive_ecosystem_metadata` and that the
   service account can query and update it.
4. Run a **read-only** preflight for the previous completed month:
   `python -m app.monthly_governance_worker --period YYYY-MM --preflight-only`.
   This checks settings and D1 connectivity/schema; it does not test downstream
   credentials or perform a monthly governance run.
5. Configure the scheduler to run **once monthly**, after the reporting month
   closes (for example 03:00 UTC on day 1), with a single active instance and
   a runtime budget longer than the expected governance cycle. Use:
   `python -m app.monthly_governance_worker --previous-month`.
   The worker resolves the **previous completed UTC month** itself, including
   January year rollover. Keep `--period YYYY-MM` for deliberate operator-led
   investigation only. Never schedule a fixed reporting period.
6. Turn off the old MAST job group's automatic POST to
   `/v1/monthly-review/generate` **before** enabling the new scheduler.
   Otherwise two independent execution paths can race, and the legacy POST
   does not participate in the worker's D1 claim.
7. Do not manually trigger a run until previous 504 activity has been reconciled.

## GitHub read-only worker preflight

The manual **Worker D1 preflight (read only)** Actions workflow runs the
worker's existing `--previous-month --preflight-only` mode in a temporary
GitHub runner. Configure the production GitHub environment with secrets
`D1_ACCOUNT_ID`, `D1_DATABASE_ID`, `D1_API_KEY`,
`CF_R2_ACCOUNT_ID`, `CF_R2_ACCESS_KEY_ID`, `CF_R2_SECRET_ACCESS_KEY`,
`AIMS_API_KEY`, `RAMS_API_KEY`, and environment variables
`R2_BUCKET_AUDITS`, `AIMS_BASE_URL`, `RAMS_BASE_URL`.
The workflow sets `D1_ENABLED=true` explicitly.

A green result confirms settings are present and that the runner can query
D1 diagnostics/schema. It **does not** establish R2 or downstream write
permissions, confirm the deployed worker has the same secrets, or install a
scheduler. Do not schedule a write-producing GitHub workflow as a substitute
for the persistent independent worker without a separate deployment decision.

## Monitoring and incident handling

- GitHub Actions: `Inspect monthly governance job (read only)` accepts
  `YYYY-MM` and calls `GET /v1/monthly-review/jobs/{period}`.
  It requires production `HIVE_BASE_URL` and `HIVE_ADMIN_TOKEN`.
- `completed`: worker reached its terminal success state. Verify the
  archived report, Council completion and downstream sync separately.
- `failed`: investigate report/downstream writes before any retry.
- `claimed`: may be running or may have crashed after partial writes.
  **Never** clear or reclaim automatically; inspect D1, R2, Council run IDs,
  AIMS/RAMS effects and worker logs first.
- `404`: no worker claim is recorded. This does **not** prove the legacy HTTP
  endpoint or another process did not execute.
- Worker exit codes: 0 completed/preflight passed; 1 report failed;
  2 claim unavailable; 3 duplicate; 4 terminal status persistence failed;
  5 missing configuration; 6 D1 preflight failed; 7 reporting month is not yet completed.

## Release acceptance checklist

- [ ] Worker container deployed with correct backend version and secrets
- [ ] Read-only preflight passes in production
- [ ] Monthly scheduler configured and observable
- [ ] Legacy MAST POST schedule disabled to prevent duplicate paths
- [ ] Existing ambiguous 504 writes reconciled before execution
- [ ] Production status endpoint can read D1 job state
- [ ] First authorized run archives R2 report, indexes D1, and verifies
      Council and downstream sync
- [ ] Alerting established for missing, claimed-too-long and failed jobs

The legacy HTTP 504 is a separate outstanding issue. Do not assume that a
timeout means the operation was rolled back.
