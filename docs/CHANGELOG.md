# HIVE Changelog
## v1.16 – Skill Search Review Integration

- Consolidated the v1.9 intelligent skill-search branch into the later review/evidence-pack code line.
- Restored the missing `shared_execution_plan` service function required by execution reviews.
- Added recommendation, routing and shared execution-plan endpoints to the active skills API.
- Updated build markers, tests, docs and smoke scripts to the v1.16 line.
- Removed stale `__pycache__`/`.pyc` artefacts and duplicate stale core service copies.
- Preserved review-gated, plan-only safety: no live execution or repo mutation.

