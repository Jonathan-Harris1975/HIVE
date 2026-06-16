> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# Changelog

## 2026.06.16 — Ecosystem health update

- Added authenticated ecosystem repository-health aggregation.
- Added HIVE-UI, AIMS, RAMS, MAST, IRS and website health configuration.
- Added production CI for tests, linting, preflight and container build.
- Refreshed production, security and operations documentation.

## v1.23-hive-ui-api-contract

- Persisted streamed `/v1/chat/stream` user and assistant turns in the SQL conversation store.
- Emitted the conversation ID before the first model token and returned persistence status in the final SSE event.
- Preserved partially generated streamed responses when a client disconnects after receiving tokens.
- Generated bounded conversation titles from the first user message.
- Added conversation rename and cascade-style delete API operations for HIVE-UI.
- Added regression coverage for streamed persistence and conversation lifecycle operations.

## v1.22-workflow-simulation-persistence

- Added deterministic pretend-mode workflow simulation via `POST /v1/workflow-simulation`.
- Added reusable execution policy profiles via `GET /v1/execution-preview/policy-profiles`.
- Added D1-backed execution preview persistence via `POST /v1/execution-preview/save`, `GET /v1/execution-preview/history`, and `GET /v1/execution-preview/{preview_id}`.
- Preserved the non-executing safety model: no adapters, repo mutation, package installs, R2 writes, or background jobs.
- Added v1.20, v1.21 and v1.22 smoke scripts plus regression tests.


# HIVE Changelog

## v1.22-workflow-simulation-persistence

- Added read-only skill-registry integrity checks.
- Added duplicate, missing-field, taxonomy and orphan/mismatch reports for imported shared skills.
- Added dry-run-first `/v1/skills/rebuild-index` maintenance endpoint.
- Preserved v1.16 skill search, recommendation, routing, review queue and evidence-pack behaviour.
- Updated docs and smoke scripts for the v1.17 build line.

## v1.16 – Skill Search Review Integration

- Consolidated the v1.9 intelligent skill-search branch into the later review/evidence-pack code line.
- Restored the missing `shared_execution_plan` service function required by execution reviews.
- Added recommendation, routing and shared execution-plan endpoints to the active skills API.
- Updated build markers, tests, docs and smoke scripts to the v1.16 line.
- Removed stale `__pycache__`/`.pyc` artefacts and duplicate stale core service copies.
- Preserved review-gated, plan-only safety: no live execution or repo mutation.


## v1.22-workflow-simulation-persistence

- Added workflow graph templates and graph-shaped plan generation for the future HIVE operator UI.
- Added controlled execution preview responses with node statuses, blockers and next required actions.
- Added `/v1/workflow-graphs/templates`, `/v1/workflow-graphs/build`, `/v1/execution-preview/policies` and `/v1/execution-preview`.
- Kept execution fully disabled: no skills are run, no repos are mutated, no packages are installed and no background jobs are started.
- Kept the system Koyeb Free friendly with bounded synchronous planning only.

## v1.18-workflow-graph-planning

- Introduced graph-shaped workflow planning as the bridge between skill routing and a future UI.
- Added standard nodes for request classification, skill recommendation, evidence collection, dry-run output, risk gate, review queue and blocked adapter execution.