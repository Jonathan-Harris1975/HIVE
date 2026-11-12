# HIVE ecosystem review — 12 November 2026

## Scope and evidence

Reviewed HIVE and HIVE-UI main branches against the governed estate: HIVE, HIVE-UI, AIMS, AIMS-UI, RAMS, MAST, IRS and Website. Checked GitHub CI/deployment evidence, public health endpoints, Cloudflare deployment metadata and the MAST durable scheduler receipts. No model inference, publication, deletion or paid render was triggered.

The eight repository IDs and GitHub sources agree between HIVE and HIVE-UI. The retired model-selection workspace is absent from production navigation. LLM spend, operational review and the chat model picker remain separate functions.

HIVE-UI's live health endpoint reported main commit `ae3cea246321cee23879354a99c1800aa8e27216`, confirming PR #215 reached production. HIVE returned healthy core configuration for OpenRouter, D1, R2, Vectorize and embeddings. AIMS and AIMS-UI readiness returned ready. MAST's private R2 heartbeat was fresh, and the latest KDP research/update-verification receipts were successful. These observations establish infrastructure readiness and execution receipts, not recipient-level email delivery or success of every content lane.

## Corrections in this change

- Expose the existing deterministic Production Manager report in Repositories, alongside the exact code commit, required workflows and deployment evidence for each repository. Indexed-code freshness is explicitly separate from production certification.
- Expire UI production decisions after two minutes, and show unknown for missing, incomplete or failed evidence. Keep the longer collection deadline limited to the exact read-only production-report route.
- Include recent, deduplicated scheduled work in the redacted MAST health summary. Exclude warmups and skipped attempts. Retain explicit execution timestamps and failed receipts even while the scheduler heartbeat is healthy.
- Reject missing, stale or implausibly future scheduler heartbeats before using lifecycle state to claim intentional standby. Fresh intentional standby remains supported.
- Surface additional failed workflows on the current commit without silently redefining the required release-gate policy. Explicitly mark additional-workflow discovery incomplete when bounded collection stops before all pages are read. The failing website ebook publication check is one concrete example.
- Document the two missing environment settings. All 241 Settings fields now have documented environment names; values match existing defaults.
- Describe the alert inbox as historical events and link to current readiness. Do not interpret clearing alerts as repairing a job.
- Do not invent R2 read permissions from legacy string-only bucket listings; report failed bucket inventory responses as unavailable.

## Outstanding operational evidence

The Website's `Ebook subsystem CI` run [43161976552](https://github.com/Jonathan-Harris1975/jonathan-harris-website/actions/runs/43161976552) failed its live books API parity check: the public API did not match the governed repository artifact. Its separate required production workflows and Website deployed integration passed. This discrepancy remains an operational warning; this change does not claim to repair the website catalogue.

Deployment evidence for the observed current commits was successful for HIVE-UI, AIMS, AIMS-UI, RAMS, MAST, IRS and Website. The observed HIVE deployment-watch run [42523328817](https://github.com/Jonathan-Harris1975/HIVE/actions/runs/42523328817) was skipped. HIVE process health alone therefore does not certify its exact deployed commit. The new report must retain incomplete evidence rather than treating it as green.

Earlier Blotato render timeout and paid-render-cap failures remain separate from scheduler liveness. Tuesday's operation had recovered, but its historical inbox alert remained stored. The review queue also retains retired jobs, so it is deliberately not treated as an authoritative active schedule.

The checking browser had no authenticated HIVE operator session. Protected dashboard state was not inspected by bypassing login. The fixes are covered by mocked contract tests and repository CI; authenticated live application verification remains a deployment acceptance step.

## Validation

HIVE: 699 tests passed, including ten subtests; coverage 74.82%, above the 74% gate. Ruff's required CI rules passed. The MyPy baseline guard reported no new regressions. Targeted tests cover stale/fresh lifecycle evidence, redacted recent receipts, exact-commit workflow warnings and environment coverage.

HIVE-UI: full `npm run check` passed; 129 unit tests, security/UX contracts, TypeScript, lint, production build and distribution verification. Added coverage for all-eight production reporting, incomplete evidence, expiry, fetch failures, independent business-job failures and the scoped proxy deadline. Local checks do not replace authenticated deployment acceptance.
