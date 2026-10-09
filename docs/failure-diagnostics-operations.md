# Failure diagnostics: expected skips and investigation

The `Deployment failure diagnostics` workflow subscribes to completed workflow runs but its `failure_details` job is intentionally conditional.

| Upstream conclusion | Expected diagnostics job |
| --- | --- |
| `success` | skipped |
| `skipped` | skipped |
| `neutral` | skipped |
| `failure` | runs |
| `cancelled` | runs |
| `timed_out` | runs |
| `action_required` | runs |
| Self-trigger from `Deployment failure diagnostics` | skipped |
| Manual dispatch with a `run_id` | runs |

**Do not interpret repeated skipped diagnostics runs as failures without checking the triggering workflow conclusion.** For a failing upstream run, verify the diagnostics job executed, produced `deployment-failure-details.md`, and uploaded the 30-day artifact. A skipped diagnostics job for a failed upstream run is anomalous and must be investigated.

The workflow checks out reporting code from the trusted default branch and has read-only `contents` and `actions` permissions. Its report must never be used alone to authorise a merge, production deployment, or automatic repair.

For historical run `37980926474`, inspect the upstream conclusion and job graph before classifying its skip. This document does not assert that the run has been independently verified.
