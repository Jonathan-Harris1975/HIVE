## 22 June 2026 - v1.26.12 catalogue metadata

- Added `skills/catalogue_metadata.json` and `tasks/task_metadata.json` as governed metadata catalogues.
- Enriched skills, workflow presets and execution workflow nodes with stable descriptions and UI-safe fallbacks.
- Added a digital-dust review manifest for superseded root patch notes and generated cache artefacts.

## 22 June 2026 - v1.26.11 env split

- Split production configuration into repo-committed non-secret defaults and Koyeb secrets-only values.
- Added `HIVE-PRODUCTION-SHARED.env` for e-medium production defaults.
- Added `HIVE-KOYEB-SECRETS-ONLY.env` for paste-ready Koyeb secret references.
- Updated startup loading so Koyeb runtime variables override the shared file.

## 22 June 2026 - v1.26.10 chat persistence sync

- Raised OpenRouter stream idle/first-token windows so healthy replies are not clipped mid-answer.
- Added streamed finish_reason and completion_truncated metadata.
- Added DATABASE_AUTO_INIT and SQL schema retry so persisted conversations self-heal when tables are missing.
- Updated Koyeb e-medium env to APP_VERSION=1.26.10-production.

## 22 June 2026 - v1.26.8 production readiness sync

- Updated backend build marker to `v1.26.10-chat-persistence-sync` and default `APP_VERSION` to `1.26.10-production`.
- Added RAMS readiness bearer-token aliasing and production readiness validation so authenticated readiness probes cannot silently report blocked.
- Added OpenRouter stream first-token and idle timeout controls for Koyeb e-medium operation.
- Confirmed execution adapters remain review-gated, allow-listed and operator-triggered after approval.
- Expanded ZIP extraction suffix/filename support for GitHub repository archives while keeping member and text limits bounded.

## 16 June 2026 production-readiness hardening

- Added bounded operational R2 readiness probes for every required lane and governed shared-skill objects.
- Added a governed R2 search-document fallback when D1 is unavailable.
- Injected bounded skill excerpts and provenance into authorised model requests while treating skill text as untrusted data.
- Restricted skill imports to the configured HTTPS source, disabled redirects and bounded response size.
- Normalised and URL-encoded public R2 object keys, rejecting direct and encoded traversal.
- Corrected AIMS and RAMS liveness defaults to `/livez`.

> **Document status:** Production reference  
> **Last reviewed:** 22 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# Changelog

## v1.26 R2 write, skill and model selection

- Added readable date/filename R2 upload keys.
- Enabled configured ecosystem R2 lanes for read/write when shared server-side write credentials allow it.
- Added inline R2 object viewing and file-to-skill registration.
- Enabled all model categories for explicit selection.

## 2026.06.20 — Production execution gates

- Updated the build marker to `v1.26-r2-write-skill-models`.
- Added the production execution adapter policy surface and health flag.
- Changed approved execution-review decisions so `can_execute_now:true`, `adapter_execution_enabled:true`, and `execution_state:ready_for_execution` are recorded.
- Changed controlled execution previews so approved plans show the production adapter handoff as ready instead of blocked.
- Kept adapter handoff operator-triggered: approval unlocks the gate but does not auto-run package installs, repo pushes or background jobs.

## 2026.06.16 — Ecosystem health update

- Added authenticated ecosystem repository-health aggregation.
- Added HIVE-UI, AIMS, RAMS, MAST, IRS and website health configuration.
- Added production CI for tests, linting, preflight and container build.
- Refreshed production, security and operations documentation.

## v1.26-r2-write-skill-models

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