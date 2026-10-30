# Monthly governance execution and operations

MAST is the ecosystem scheduling authority. On the first day of each month it
calls HIVE's authenticated `POST /v1/monthly-review/generate`. HIVE validates
the completed reporting period, atomically claims it in D1 and returns HTTP 202.
The HIVE service then runs the AI Council cycle, AIMS/RAMS propagation, report
assembly, R2 archive and D1 indexing in a background task. MAST polls
`GET /v1/monthly-review/jobs/{period}` until the job reaches `completed` or
`failed`.

This keeps the long governance workflow off the request-response path that
caused gateway 504s. It uses the existing always-on HIVE service; it does not
require a second scheduled or paid worker.

## Configuration and readiness

The production HIVE service must have its existing D1, R2 and downstream AIMS
and RAMS settings configured. The authenticated monthly review endpoint fails
closed when D1 cannot claim the job. The existing
**Worker D1 preflight (read only)** GitHub Actions workflow can still verify D1
connectivity and schema from GitHub-hosted infrastructure. It is diagnostic
only; it does not schedule or execute governance.

The standalone command
`python -m app.monthly_governance_worker --previous-month` remains available
for controlled operator-led recovery or a deliberate one-shot execution.
Do not configure it as a second recurring schedule while MAST owns the monthly
run.

## Duplicate and ambiguous request handling

- A `completed` status means the background operation reached a successful
  terminal state.
- A `failed` status means the report or one of its required downstream/archive
  steps failed; inspect Council history, R2, D1 and AIMS/RAMS before any retry.
- A `claimed` status may mean the operation is running or stopped after partial
  writes. Never clear or reclaim it automatically. Reconcile report writes
  and worker logs before a manual retry.
- MAST disables retries for the monthly POST and consumes the monthly schedule
  window after a terminal or transport failure. A timeout is not evidence that
  the operation rolled back.
- HTTP 409 means a D1 claim already exists. Inspect
  `GET /v1/monthly-review/jobs/{period}`; do not issue another POST.
- HTTP 404 from the status endpoint means there is no recorded D1 claim. This
  does not prove another execution path had no side effects.

The manual **Verify live monthly governance** workflow also starts once and
polls D1 status. It requires explicit `execute=true`; if the start response is
ambiguous or polling expires with `claimed`, it exits without retrying.

## Operational acceptance

- [ ] MAST's deployed revision includes the monthly async job and has a fresh R2 heartbeat.
- [ ] HIVE's deployed revision returns 202 from the authenticated monthly start endpoint.
- [ ] HIVE D1 contains the `hive_ecosystem_metadata` lane and records the job claim.
- [ ] The status endpoint reports terminal state and MAST records the poll result.
- [ ] The archived report exists in R2, is indexed in D1, and records verified Council completion and AIMS/RAMS propagation.
- [ ] Any previous ambiguous 504 is reconciled before a manual retry.
