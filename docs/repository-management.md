# Repository Overview, Memory, Intelligence and governed remediation

**Status:** Production contract  
**Last reviewed:** 21 September 2026

HIVE governs one canonical eight-repository estate: `HIVE`, `HIVE-UI`, `AIMS`, `AIMS-UI`, `RAMS`, `MAST`, `IRS`, and `Website` (`jonathan-harris-website` is accepted as the Website source alias). The canonical catalogue lives in `app.core.governed_repositories`; API validation, GitHub refresh and production readiness derive from that definition rather than carrying separate copies.

## Repository ingestion

`POST /v1/repositories` remains the backwards-compatible single-ZIP route. `POST /v1/repositories/bulk` accepts multiple ZIPs as the `uploads` multipart field. Bulk ingestion enforces `REPOSITORY_BULK_MAX_COUNT`, the normal per-repository upload limit, `REPOSITORY_BULK_MAX_TOTAL_BYTES`, ZIP traversal/bomb/file-count protections, and `REPOSITORY_BULK_CONCURRENCY`. Each archive receives an independent result. One invalid archive does not roll back successful repositories in the same request.

Bulk outcomes are `success`, `failed`, `duplicate`, `unchanged`, or `updated`. Successful/unchanged/updated items return the canonical repository ID, current fingerprint and indexed version. Exact duplicate archive bytes within one request are reported as `duplicate`. Re-uploading the same repository snapshot is idempotent and returns `unchanged`; a different accepted fingerprint returns `updated`.

Every accepted snapshot follows the governed pipeline: safe extraction, canonical identity, fingerprint/manifest, durable repository snapshot/manifest where configured, Repository Memory, Repository QA, Repository Council, Project DNA/Learning, Repository Intelligence and AI Search/indexing. Validation errors occur before durable file or metadata persistence.

## Snapshot and freshness contract

Current Repository Memory and Intelligence are snapshot-specific. Each current artefact records the canonical repository ID, source filename, fingerprint, indexed version, refresh timestamp, and source commit SHA when it can be derived from the source. A history entry may remain available for auditability, but it is not treated as current unless its fingerprint matches the registered latest snapshot.

`GET /v1/repositories` includes snapshot-aware readiness on each loaded repository. `GET /v1/repositories/estate/readiness` returns a machine-readable view of all eight governed repositories, including snapshot, Memory, QA, Council, Intelligence, AI Search/index status, current fingerprint/indexed version, latest refresh timestamp, last pipeline failure and whether repair/setup is required.

`POST /v1/repositories/refresh-all` processes configured governed repositories independently. The job is complete only after every configured repository has a terminal result. Each result exposes download and ingestion status plus pipeline component state. The overall job cannot report success while a governed repository has failed or non-current required intelligence.

After restart/rehydration, snapshot identity is preserved from the durable manifest. Reindex/setup and GitHub refresh must rebuild current Memory/Intelligence rather than allowing an older report to become current by accident.

## Controlled repository improvements

The amount of repository work and the Council quality tolerance are separate controls.

`REPOSITORY_IMPROVEMENT_MAX_CHANGE_RATIO=0.12` means one work pass may modify/delete at most 12% of eligible modifiable files. Eligible files exclude `.git`, dependency/vendor directories, generated outputs, protected environment/secret material, binary assets unless a finding explicitly targets them, and other paths already prohibited by improvement safety rules. The percentage limit uses a minimum of one file for small repositories and is additionally capped by `REPOSITORY_IMPROVEMENT_MAX_CHANGE_FILES`. The safer limit wins.

Every improvement report records eligible file count, configured ratio, effective file limit, files changed/deleted and actual changed ratio. A model response that exceeds the pass budget is rejected before promotion.

`POST /v1/repositories/{repository_id}/improvements/run` accepts an optional JSON body:

```json
{
  "execution_mode": "single_pass",
  "max_work_passes": 1
}
```

Use `execution_mode="multi_pass"` for work that legitimately exceeds one 12% pass. Each work pass starts from the previous accepted candidate, handles a bounded coherent finding subset, reruns static/security Repository QA, records a per-pass evidence ledger and carries remaining findings forward. The job stops when findings are cleared, a hard blocker/no-progress condition occurs, cancellation is requested, or the configured finite pass cap is reached. Work-pass counters are distinct from model self-improvement loop counters and Repository Council run counters.

`POST /v1/repositories/{repository_id}/improvements/jobs/{job_id}/cancel` cancels an active job. Successful final output contains a changed-files ZIP, full updated-repository ZIP, work-pass ledger, changed/deleted files, models used, per-pass QA/security results, remaining findings, required external CI verification and cumulative change count/ratio.

The Council near-threshold tolerance remains `REPOSITORY_IMPROVEMENT_NEAR_THRESHOLD_TOLERANCE` (default `0.05`). It affects only Council acceptance after the normal improvement loops fail the full target. It does not increase the number of files HIVE may change in a pass and the 12% work budget does not lower quality/security gates. The neutral report field is `accepted_under_near_threshold_tolerance`; legacy `accepted_under_5_percent_rule` is retained for persisted-consumer compatibility.

## Production configuration and recovery

Relevant non-secret configuration:

```env
REPOSITORY_BULK_MAX_COUNT=8
REPOSITORY_BULK_MAX_TOTAL_BYTES=838860800
REPOSITORY_BULK_CONCURRENCY=2
REPOSITORY_IMPROVEMENT_MAX_CHANGE_RATIO=0.12
REPOSITORY_IMPROVEMENT_MAX_CHANGE_FILES=100
REPOSITORY_IMPROVEMENT_MAX_WORK_PASSES=4
REPOSITORY_IMPROVEMENT_NEAR_THRESHOLD_TOLERANCE=0.05
```

Production readiness requires the governed GitHub refresh catalogue to contain exactly the canonical eight repositories when refresh is enabled. It also verifies that bulk capacity can accept the estate and that the repository work-scope configuration cannot exceed 12% per pass.

If a repository shows `stale`, `not_ready`, or `repair_required=true`, first compare its current fingerprint with the Memory/Intelligence fingerprint. Re-run repository setup/reindex for the current accepted snapshot or re-upload/refresh the governed source as appropriate. Do not copy an older intelligence record forward merely to clear readiness. Failed bulk/refresh items are independent and may be retried without discarding successful sibling results.

## Repository UI progressive-disclosure contract

The HIVE-UI repository workspace may present repository metadata, diagnostics, dependency information, diff controls, improvement execution settings, QA evidence, Council evidence, learning data, and history through collapsed disclosure sections. This is a presentation-only hierarchy: collapsing a section must not suppress, defer, or alter repository registration, Memory, Intelligence, QA, Council, refresh, diff, reindex, setup, or improvement API behaviour.

Primary repository health and readiness state should remain visible without expansion. Detailed diagnostics and destructive or infrequent actions may be placed behind explicit "More information & actions" or equivalent disclosure controls. The existing `/v1/repositories` API contracts remain authoritative; no backend API change is required for this UI organisation.
