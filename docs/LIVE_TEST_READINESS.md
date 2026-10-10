# Controlled live-test readiness

Baseline: main commit `20d87ef79d5918d2982e169afaa020146cb8e41e` (2026-10-10; checked against GitHub).
Status: **NOT READY FOR CONTROLLED LIVE TESTING** until every blocking gate below has recorded evidence.

| Gate | Current evidence | Status |
|---|---|---|
| Immutable evidence writes | PR #403 proposes signed conditional writes and regression tests; unmerged | Pending CI and R2 integration |
| Exact run binding | `r2_evidence_store.py` checks SHA and run identifiers | Partial; external identity binding unverified |
| Concurrent mutation leases | `r2_mutation_lease.py` uses conditional ETag writes | Needs live race and failure injection |
| Stale-holder fencing | Generation and token stored; downstream enforcement not proven | Blocked |
| R2 outages / clock skew | No recorded controlled test | Blocked |
| Weekend orchestration | `test_weekend_orchestrator.py` contains offline scenarios | Needs full end-to-end run |
| Downstream AIMS/MAST/RAMS/IRS/UI signals | No exact-SHA, fail-closed receipts verified in this review | Blocked |
| Ecosystem OIDC | Provider trust and per-repository claims not independently verified | Blocked |
| CI/security and required review | PR #403 and #404 have successful PR CI, CodeQL and security workflow conclusions; review/merge and post-merge main checks outstanding | Pending |
| Rollback / incident escalation | No executed rehearsal evidence | Blocked |

## Reproducible offline test commands

```bash
python3 -m unittest discover -s .github/scripts -p 'test_r2_evidence*.py' -v
python3 -m unittest discover -s .github/scripts -p 'test_r2_mutation_lease.py' -v
python3 -m unittest discover -s .github/scripts -p 'test_weekend_orchestrator.py' -v
```

## Controlled validation procedure

1. Obtain required reviews, a green exact-head CI run, and an operator-approved non-production target. Record candidate SHA and deployed artifact digest.
2. Run the R2 live-validation workflow with scoped credentials. Confirm the exact run's object exists, then attempt an identical write and require a rejected overwrite (HTTP 412). Preserve redacted run URLs and object digest.
3. Concurrently acquire one fingerprint from two isolated workers; exactly one must win. Test expired holders, delayed requests, stale ETags, clock skew, R2 timeout/5xx and process termination. Any uncertainty must deny mutation.
4. Confirm downstream mutation consumers reject stale generation/token and mismatched repository/SHA/environment. Confirm every required AIMS/MAST/RAMS/IRS/UI signal is exact-SHA and fail-closed, including skipped jobs.
5. Validate ecosystem-wide OIDC audience, issuer, subject and repository/environment claims at the identity provider. Legacy ecosystem smoke tests do not substitute for this.
6. Dry-run weekend phases and Council dispatch, inject missing/replayed receipts and failed downstream workflows, and confirm no production mutation occurs.
7. Perform one approved non-production live rehearsal. Capture logs, outcomes, immutable evidence, monitoring alert, incident contact and rollback result.
8. Change status to READY only after independent evidence for every gate has been reviewed.

**Abort and rollback** on any duplicate mutation, stale-holder acceptance, missing identity proof, unexpected production write, evidence overwrite, or unbounded retry. Disable the relevant dispatch, revoke temporary credentials, restore the last verified deployment and preserve incident evidence.

This ledger deliberately does not convert skipped checks or unexecuted scenarios into passing results.

## 2026-10-10 verified GitHub checkpoint

- Default branch: `main`, commit `20d87ef79d5918d2982e169afaa020146cb8e41e`.
- PR #403 (draft): https://github.com/Jonathan-Harris1975/HIVE/pull/403 ; head `5e222844e767d1f571ae048e277984c186284c71`. PR CI https://github.com/Jonathan-Harris1975/HIVE/actions/runs/38069940295 ; CodeQL https://github.com/Jonathan-Harris1975/HIVE/actions/runs/38069940217 ; Security https://github.com/Jonathan-Harris1975/HIVE/actions/runs/38069940280 : completed/success. These are PR-head checks, **not** merged-main or provider proof.
- PR #404: https://github.com/Jonathan-Harris1975/HIVE/pull/404 ; head `9cfdd4c1666056b146d64a89af345b8732ee5ca7`. PR CI https://github.com/Jonathan-Harris1975/HIVE/actions/runs/38083366271 ; CodeQL https://github.com/Jonathan-Harris1975/HIVE/actions/runs/38083366261 ; Security https://github.com/Jonathan-Harris1975/HIVE/actions/runs/38083366344 : completed/success. JWT signature validation does **not** establish provider trust.
- PR #402: https://github.com/Jonathan-Harris1975/HIVE/pull/402 ; dependency update; reconcile overlap with #404 before merging.
- Owner for repository integration and protected-branch review: HIVE repository maintainer. Owners for provider OIDC trust and downstream atomic fencing: corresponding provider administrator and downstream mutation service maintainers (individuals not verified).

### Four-area acceptance ledger

| Area | Requirement | Implementation reference | Verification | Status | Remaining dependency / owner |
|---|---|---|---|---|---|
| Autonomous detection | Exact-head failures, skipped runs, deduplication and escalation | `.github/workflows/autonomous-repair.yml` | Controlled workflow-run failure matrix not yet witnessed | blocked | HIVE maintainer |
| Self-repair | Atomic R2 lease acquisition/renewal/release and stale-holder fencing | `.github/scripts/r2_mutation_lease.py` | Code inspected; downstream atomic fencing not demonstrated | blocked | HIVE and downstream maintainers |
| Production safeguards | Non-overwrite evidence, authenticated Council verdict and OIDC | `.github/scripts/r2_evidence_store.py`, `.github/workflows/council.yml`, PRs #403/#404 | PR checks green; R2 replay, provider trust and merged-main runs unverified | blocked | HIVE maintainer / R2 and identity administrators |
| Ecosystem coordination | Exact SHA and deployment identity across AIMS, AIMS-UI, HIVE-UI, IRS, MAST, RAMS and website | `.github/workflows/weekend-orchestrator.yml`, `.github/workflows/weekend-phase-launcher.yml` | End-to-end receipts and non-production rehearsal absent | blocked | Ecosystem repository maintainers |

Do not promote this ledger to READY until all mandatory gates have exact-run URLs, observed results, and responsible approvals. No production mutation is authorised by this document.
