# Contributing to HIVE

HIVE is a production operator service, so changes should preserve API behaviour, deployment safety, and repository evidence. Prefer small, reviewable changes over broad refactors.

## Supported environment

Python 3.11 through 3.14 is supported. The production Docker image uses Python 3.14.6 and the Koyeb buildpack fallback uses 3.11.15; CI tests all four supported minor versions.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
```

## Required quality gate

Run the same checks used by CI before merging:

```bash
PYTHONPATH=backend APP_ENV=test python -m compileall -q backend/app backend/tests scripts
PYTHONPATH=backend APP_ENV=test python -m pytest backend/tests -q --tb=short \
  --cov=app --cov-report=term-missing --cov-fail-under=74
PYTHONPATH=backend python -m ruff check backend/app backend/tests scripts --select E4,E7,E9,F
PYTHONPATH=backend python -m mypy backend/app --no-incremental --show-error-codes
python -m bandit -q -r backend/app -ll
python -m pip_audit -r requirements.txt
```

Changes to authentication, uploads, ZIP handling, storage permissions, repository execution policy, or secret/configuration handling require focused regression tests in addition to the full suite.

## Dependency changes

1. Change direct production pins in `requirements.in`.
2. Regenerate the compiled lock with `python -m piptools compile --output-file=requirements.txt --strip-extras requirements.in`.
3. Run `python -m pip check` and `python -m pip_audit -r requirements.txt`.
4. Run the full test and static-analysis gates.
5. Record security-relevant upgrades in `docs/CHANGELOG.md`.

Do not hand-edit transitive versions without also updating `requirements.in` where a deliberate direct security floor is required. Dependabot covers pip, Docker, and GitHub Actions weekly, but automated updates still require CI evidence.

## Secrets and configuration

Never commit live credentials. `.env`, `.env.*`, private-key/certificate containers (`*.pem`, `*.key`, `*.p12`), caches, and local databases are ignored. `.env.example` contains documentation-only values. `HIVE-PRODUCTION-SHARED.env` is limited to non-secret production defaults; Koyeb secret references belong in `HIVE-KOYEB-SECRETS-ONLY.env`.

Do not set `FORWARDED_ALLOW_IPS=*` casually. HIVE defaults Uvicorn to loopback-only trusted proxies. Koyeb authentication throttling has a separate bounded rule for the platform-certified final `X-Forwarded-For` hop.

## Maintainability

When changing a large module, prefer extracting cohesive helpers with tests rather than extending already-long route/service functions. The current refactoring priorities are `backend/app/api/files.py`, `backend/app/services/skill_registry.py`, `backend/app/storage/sql_store.py`, and `backend/app/core/config.py`. Preserve public API contracts and split work behind regression coverage.
