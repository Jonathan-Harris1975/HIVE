# HIVE full live-testing handoff (execution checklist)

**State:** repository-side changes under review; not production-certified. Run against an approved immutable release SHA. Record UTC timestamps, GitHub run URLs, artifact digests and responsible operator for each step.

## Prerequisites and stop conditions

- [ ] **GitHub administrator:** Confirm the active `main` ruleset's required contexts against checks emitted by an actual PR. Never remove required checks merely to merge.
- [ ] **Release engineer:** Confirm CI, CodeQL, security, image scan and deployment-watch evidence all reference the same intended SHA. Reject missing, stale, skipped-required or mismatched evidence.
- [ ] **Platform operator:** Confirm Koyeb deployment identity, deployed image digest, live health endpoints, known-good rollback revision and access to operational logs.
- [ ] **Governance operator:** Confirm D1 and R2 identities, permissions, existing monthly period state, backup/restore strategy and production environment approval. Never interpret a missing worker job as proof that legacy execution did not write.
- [ ] **Automation owner:** Confirm GitHub App installation permissions, finite retry policy, repair PR labels, Kilo trigger configuration and escalation ownership.

## Numbered weekend proving plan

1. **Release engineer:** Capture the `main` SHA and enumerate eight governed repository names and SHAs from the authoritative configuration. Fail if any repository is missing or its default branch changed.
2. **Automation owner:** Manually dispatch `weekend-orchestrator.yml` with `dry_run=true` and `skip_time_guard=true`. Record the run URL and retained JSON orchestration artifact. A dry run must not dispatch Council or merge repair PRs.
3. **Release engineer:** Check the phase launcher resolves the Saturday CI, Sunday DAST and Sunday Council windows correctly across GMT/BST transitions. Confirm phase ordering and reject early Council dispatch.
4. **Release engineer:** For each governed repository, verify the expected SHA, CI/CodeQL/security conclusions, deployment evidence and per-repository timeout/partial-failure classification. A green dispatcher is insufficient.
5. **Automation owner:** Exercise a safe synthetic failed-run fixture in a non-production test environment. Verify diagnosis, one bounded repair PR, required checks, guarded merge decision and human hold after retry exhaustion. Do not deliberately fail live production.
6. **Platform operator:** Verify deployed SHA and image digest, application health and rollback revision. Stop on any mismatch or unverified dependency; restore the last known-good deployment through the provider's approved procedure.

## Numbered monthly governance proving plan

7. **Governance operator:** Choose a previously completed `YYYY-MM` period and inspect existing D1 job state and R2 objects before any write. Record evidence and reconcile legacy HTTP executions.
8. **Governance operator:** Dispatch `preflight-live-monthly-governance.yml` with the chosen period. This is GET-only; require healthy configuration and investigate any pre-existing job state.
9. **Governance operator:** Inspect `inspect-monthly-governance-job.yml` and the read-only R2 evidence validation workflow; verify worker connectivity, access controls and object integrity without mutation.
10. **Governance operator:** Only after explicit approval and confirmed idempotency, dispatch `verify-live-monthly-governance.yml` with `execute=true`. This POST can write downstream data and is **not** part of dry-run proving.
11. **Governance operator:** Confirm section totals, Council completion, D1 indexing, R2 archival and downstream synchronisation from independent evidence. Record exact object IDs and checksums, not merely workflow success.

## Rollback, escalation and acceptance

- **Stop and escalate:** any required check missing/failing; stale SHA; unexpected repair loop; partial repository evidence; governance period already processed without reconciliation; D1/R2 mismatch; authentication failure; or health degradation.
- **Rollback owner:** platform operator for deployed revision; governance operator for downstream reconciliation. Do not delete D1/R2 evidence or replay non-idempotent writes automatically.
- **Success:** all eight repository records complete; CI/security and functional smoke checks passed on the intended SHA; deploy identity and rollback verified; weekend and monthly governance evidence complete; no unresolved critical findings.
- **Handoff:** classify each item as **verified**, **implemented but awaiting live verification**, or **blocked** with the precise external prerequisite. Until the above is observed, status remains **awaiting full live testing**.
