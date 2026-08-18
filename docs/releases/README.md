# Historical release artefacts

Files in this directory are retained as release history and **must not be used as current deployment configuration**. Some pre-audit environment snapshots contain settings that have since been superseded, including broad forwarded-header trust.

For current deployment values use, in order:

1. `HIVE-PRODUCTION-SHARED.env` for non-secret production defaults.
2. `HIVE-KOYEB-SECRETS-ONLY.env` for Koyeb secret references.
3. `docs/koyeb-deployment.md`, `docs/production-readiness.md`, `SECURITY.md`, and `README.md` for the active operational contract.

Historical files are intentionally not rewritten because doing so would corrupt the release record.
