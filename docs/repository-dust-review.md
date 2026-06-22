> **Document status:** Current cleanup review  
> **Last reviewed:** 22 June 2026  
> **Build marker:** `v1.26.12-catalogue-metadata`

# HIVE repository dust review

This review separates production source files from superseded patch notes, generated cache artefacts and old env snapshots. The cleanup is intentionally conservative: current production docs, shared env files, tests, source code and release history under `docs/releases/` stay in the repo.

## Removed from the root

The following root-level files were superseded by `README.md`, `docs/CHANGELOG.md`, `docs/production-readiness.md`, `HIVE-PRODUCTION-SHARED.env` and `HIVE-KOYEB-SECRETS-ONLY.env`:

- `HIVE-KOYEB-PRODUCTION-ENV.txt`
- `HIVE-REPO-HEALTH-KOYEB-ENV-PATCH.txt`
- `HIVE-v1.24-KOYEB-ENV-PATCH.txt`
- `HIVE-v1.25-PRODUCTION-EXECUTION-GATES-PATCH.txt`
- `HIVE-v1.26-R2-WRITE-SKILL-MODELS-PATCH.txt`
- `HIVE-v1.26.2-FILE-SKILL-APPLY-FLOW-PATCH.txt`
- `HIVE-v1.26.3-SKILL-CATALOGUE-CLEANUP-PATCH.txt`
- `HIVE-v1.26.4-HELPER-ORCHESTRATION-PATCH.txt`
- `HIVE-v1.26.5-PRODUCTION-ENV-GITHUB-ZIP-PATCH.txt`
- `HIVE-v1.26.7-KOYEB-E-MEDIUM-FASTLANE-ENV-PATCH.txt`
- `HIVE-v1.26.7-STREAM-FASTLANE-PATCH.txt`
- `HIVE-v1.26.8-CI-PREFLIGHT-RAMS-AUTH-PATCH.md`
- `HIVE-v1.26.8-CI-TEST-SYNC-NOTES.txt`
- `HIVE-v1.26.8-KOYEB-E-MEDIUM-ENV.txt`
- `HIVE-v1.26.8-VERSION-READINESS-ADAPTER-PATCH.txt`
- `HIVE-v1.26.9-KOYEB-E-MEDIUM-ENV.txt`
- `HIVE-v1.26.9-REVIEW-STATE-SYNC-PATCH.txt`
- `HIVE-v1.26.10-CHAT-PERSISTENCE-SYNC-PATCH.txt`
- `HIVE-v1.26.10-KOYEB-E-MEDIUM-ENV.txt`
- `HIVE-v1.26.11-CI-BUILD-MARKER-SYNC-PATCH.md`
- `HIVE-v1.26.11-ENV-SPLIT-PATCH.txt`

## Generated artefacts removed

- Python `__pycache__/` directories
- `.pytest_cache/`

## Kept deliberately

- `HIVE-PRODUCTION-SHARED.env`
- `HIVE-KOYEB-SECRETS-ONLY.env`
- `HIVE-PRODUCTION-READINESS-REPORT.md`
- `FROZEN_FILES_SHA256.txt`
- `V1.24_FROZEN_FILES_SHA256.txt`
- release notes under `docs/releases/`

The two frozen hash manifests are kept for now because they may still be useful historical deployment evidence. They can be removed in a later dedicated cleanup if no workflow references them.
