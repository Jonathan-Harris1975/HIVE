# HIVE Changelog

## v1.19-controlled-execution-preview

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


## v1.19-controlled-execution-preview

- Added workflow graph templates and graph-shaped plan generation for the future HIVE operator UI.
- Added controlled execution preview responses with node statuses, blockers and next required actions.
- Added `/v1/workflow-graphs/templates`, `/v1/workflow-graphs/build`, `/v1/execution-preview/policies` and `/v1/execution-preview`.
- Kept execution fully disabled: no skills are run, no repos are mutated, no packages are installed and no background jobs are started.
- Kept the system Koyeb Free friendly with bounded synchronous planning only.

## v1.18-workflow-graph-planning

- Introduced graph-shaped workflow planning as the bridge between skill routing and a future UI.
- Added standard nodes for request classification, skill recommendation, evidence collection, dry-run output, risk gate, review queue and blocked adapter execution.
