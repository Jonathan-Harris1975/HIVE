# Controlled live-test readiness

Baseline: main commit `52fe2c12f7d4db86db29522e14b784512cffcee3` (2026-10-10).
Status: **NOT READY FOR CONTROLLED LIVE TESTING** until every blocking gate below has recorded evidence.

| Gate | Current evidence | Status |
|---|---|---|
| Immutable evidence writes | PR #400 uses signed `If-None-Match: *` and regression tests | Pending CI and R2 integration |
| Exact run binding | `r2_evidence_store.py` checks SHA and run identifiers | Partial; external identity binding unverified |
| Concurrent mutation leases | `r2_mutation_lease.py` uses conditional ETag writes | Needs live race and failure injection |
| Stale-holder fencing | Generation and token stored; downstream enforcement not proven | Blocked |
| R2 outages / clock skew | No recorded controlled test | Blocked |
| Weekend orchestration | `test_weekend_orchestrator.py` contains offline scenarios | Needs full end-to-end run |
| Downstream AIMS/MAST/RAMS/IRS/UI signals | No exact-SHA, fail-closed receipts verified in this review | Blocked |
| Ecosystem OIDC | Provider trust and per-repository claims not independently verified | Blocked |
| CI/security and required review | PR #400 checks and merge protections in progress | Pending |
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
