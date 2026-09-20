# HIVE Focused Maintenance Report

Date: 20 September 2026

## Files changed

- `requirements.in`
- `requirements.txt`
- `backend/app/core/config.py`
- `backend/tests/test_settings_env_aliases.py`
- `README.md`
- `docs/CHANGELOG.md`
- `docs/production-readiness.md`

## Dependency refresh

- `pydantic-settings`: 2.14.2 -> 2.15.0
- `boto3`: 1.43.41 -> 1.43.98
- `botocore`: 1.43.41 -> 1.43.98
- Existing compatible pins retained: `pydantic==2.13.5`, `pydantic-core==2.46.5`, `s3transfer==0.19.0`, `jmespath==1.1.0`, `urllib3==2.7.0`, `python-dateutil==2.9.0.post0`, `python-dotenv==1.2.2`, `typing-inspection==0.4.2`.

## Compatibility changes

- Added explicit `case_sensitive=False` to HIVE's `SettingsConfigDict` to make the intended case-insensitive settings-source policy explicit under pydantic-settings 2.15.
- Added a regression assertion for that settings policy.
- No R2, S3-compatible storage, embeddings, startup, shutdown, API or Docker-entrypoint application code needed behavioural changes.

## Validation completed in this environment

- Python compilation: PASS.
- Direct dependency lock consistency (`python scripts/verify_dependency_lock.py`): PASS.
- Full pytest suite: **488 passed**.
- Coverage: **78.66%**, above the repository's 74% floor.
- Focused R2/embeddings regression: **26 passed**.
- Settings/environment regression: **11 passed**.
- Production-readiness tests: **17 passed**.
- Secret scan: PASS, no committed literal credentials detected.
- Performance gate: PASS.
  - `/livez`: 0 errors; mean 0.414 ms; p95 0.692 ms.
  - `/v1/system/runtime-stats`: 0 errors; mean 1.097 ms; p95 1.518 ms.
- Local startup/health smoke using `scripts/start.sh`: PASS for `/livez`, `/health`, and authenticated runtime stats.
- Refreshed dependency metadata compatibility check: PASS for all published version constraints used by pydantic-settings 2.15.0, boto3 1.43.98 and botocore 1.43.98.

## Security/dependency evidence

- Repository secret scan passed.
- PyPI metadata for pydantic-settings 2.15.0, boto3 1.43.98 and botocore 1.43.98 reports no listed vulnerabilities.
- A full repository `pip-audit` could not be executed because the sandbox does not include `pip-audit` and cannot reach the package index to install the repository's development toolchain.

## Environment-blocked gates

The following repository-defined gates could not be completed in this sandbox:

- Canonical `pip-tools` lock regeneration/`python scripts/verify_dependency_lock.py --compile`: `piptools` is not installed and package-index network access is unavailable.
- Clean isolated dependency installation: package-index network access is unavailable.
- Ruff, MyPy and Bandit: corresponding development tools are not installed and cannot be fetched.
- Full `pip-audit`: tool is not installed and cannot be fetched.
- Docker build and Docker container smoke: Docker is not available in the sandbox.
- Host-global `pip check` is not a valid HIVE result because the shared environment contains an unrelated `moviepy`/`pillow` conflict.

## Documentation reconciliation

- Root README now records the refreshed pydantic-settings and boto3/botocore versions and the settings-source compatibility policy.
- Production-readiness documentation now records the same final dependency/configuration behaviour.
- Changelog now records the focused refresh and compatibility adjustment.
- Historical audit documents were left unchanged so dated evidence remains historical rather than being rewritten.
- No stale `pydantic-settings==2.14.2`, `boto3==1.43.41` or `botocore==1.43.41` references remain in the active repository files.

## Remaining maintenance constraint

Run the repository's canonical CI in a networked environment with the declared development toolchain. In particular, `pip-compile`, `pip-audit`, Ruff, MyPy, Bandit and the Docker job must be green before treating this refresh as fully release-attested.

## Production-readiness status

**Conditionally ready, pending the environment-blocked canonical CI gates above.** The application regression suite, targeted storage/provider tests, coverage floor, secret scan, performance gate and local runtime smoke are green, and the refreshed dependency constraints are compatible according to authoritative package metadata. Full production sign-off still depends on canonical lock regeneration verification, full dependency audit, static/security tooling and Docker CI.
