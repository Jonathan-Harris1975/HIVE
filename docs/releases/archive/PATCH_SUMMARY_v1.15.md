> **Document status:** Historical implementation record  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# HIVE v1.15 Review Evidence Pack

Build marker: `v1.15-review-evidence-pack`

## Added

- `GET /v1/execution-reviews/{plan_id}/audit-trail`
- `GET /v1/execution-reviews/{plan_id}/evidence-pack`
- `POST /v1/execution-reviews/{plan_id}/export`
- Inline JSON and Markdown evidence pack export
- v1.15 Python smoke script
- v1.15 tests
- Documentation updates

## Safety

v1.15 is still plan/review only. Evidence packs do not execute skills, mutate repos, write exports to R2 or start background jobs.

## Cleanup note

Delete generated cache artefacts if present:

- `backend/app/__pycache__/1`
- `backend/.pytest_cache/`
- any `__pycache__/` directories
