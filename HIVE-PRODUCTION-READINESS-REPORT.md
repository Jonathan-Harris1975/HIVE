> **Document status:** Historical implementation record  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# HIVE Backend Production Readiness Report

**Release:** 1.25.0-production  
**Prepared:** 16 June 2026  
**Target:** Koyeb eco-micro, Cloudflare Pages HIVE-UI client

## Decision

The backend is ready for a controlled production deployment once the required Koyeb secrets and exact production origins/hosts are configured. Startup now fails closed when mandatory production configuration is missing.

## Production hardening delivered

### Configuration and deployment gates

- Added explicit production environment validation.
- Rejects the local placeholder bearer token in production.
- Requires an admin token of at least 32 characters.
- Rejects wildcard, localhost, and non-HTTPS production CORS origins.
- Validates required OpenRouter, primary R2, database, D1, Vectorize, and embeddings configuration according to feature flags.
- Validates that the total request-body allowance accommodates the upload limit.
- Provides a secret-free pre-deployment check through `scripts/production_preflight.py`.

### Runtime health and observability

- Added `GET /livez` for process liveness.
- Added `GET /readyz` for safe configuration readiness.
- Added authenticated `GET /v1/runtime/readiness` for detailed redacted checks.
- Added validated or generated request IDs.
- Added structured request-completion logs with status and elapsed time.
- Added a safe global 500 response that does not expose exception details.

### HTTP and API security

- Uses constant-time bearer-token comparison.
- Adds `WWW-Authenticate: Bearer` to missing-token responses.
- Disables OpenAPI, Swagger UI, and ReDoc in production unless deliberately enabled.
- Adds production security headers and `Cache-Control: no-store`.
- Adds trusted-host enforcement when exact hosts are configured.
- Restricts CORS methods and headers to the HIVE API contract.
- Adds an application-level request-body limit before route processing.
- Preserves server-sent event streaming by using pure ASGI middleware rather than buffering middleware.

### Container and process behaviour

- Uses a multi-stage Python 3.11 container build.
- Runs as an unprivileged `hive` user.
- Uses one worker by default for the Koyeb eco-micro footprint.
- Adds concurrency, backlog, keep-alive, forwarded-header, and graceful-shutdown controls.
- Removes the Uvicorn server banner and duplicate access logs.
- Adds an internal `/livez` container health check.
- Uses SIGTERM for orderly shutdown.

### Dependencies and continuous integration

- Added a reviewed direct-dependency source file: `requirements.in`.
- Added an exact runtime dependency lock: `requirements.txt` and `requirements.lock`.
- Added separate development and verification dependencies in `requirements-dev.txt`.
- Removed inaccessible private package-registry URLs.
- Added GitHub Actions gates for tests, Ruff, Bandit, compilation, dependency audit, Docker build, and non-root runtime verification.
- Added Dependabot configuration for Python packages and GitHub Actions.

## Verification completed

| Gate | Result |
|---|---:|
| Full backend test suite | 127 passed |
| Production-hardening tests | Passed |
| Ruff targeted quality gate | Passed |
| Bandit targeted security gate | Passed |
| Python compile check | Passed |
| Clean dependency installation | Passed |
| `pip check` dependency consistency | Passed |
| Python 3.11 wheel-availability check | Passed for 45 packages |
| Live production-mode Uvicorn smoke test | Passed |
| Liveness endpoint | 200 |
| Readiness endpoint | 200 with valid configuration |
| Authenticated readiness endpoint | 200 |
| Security headers and request IDs | Verified |
| Graceful shutdown logging | Verified |
| Frozen owner-listed files | SHA-256 unchanged |

## CI-only gates

Two checks could not be executed in the local build environment:

1. `pip-audit` could not reach its vulnerability service because direct package-index DNS was unavailable.
2. A local Docker build could not run because the Docker CLI/daemon was unavailable.

Both checks are mandatory in `.github/workflows/backend-ci.yml`. A production deployment should proceed only after the GitHub Actions workflow is green.

## Required Koyeb environment

```env
APP_ENV=production
APP_VERSION=1.25.0-production
ADMIN_BEARER_TOKEN=<unique random value of at least 32 characters>
CORS_ORIGINS=https://<your-hive-ui-domain>
ALLOWED_HOSTS=<your-service>.koyeb.app
API_DOCS_ENABLED=false
SECURITY_HEADERS_ENABLED=true
REQUEST_LOGGING_ENABLED=true
TRUSTED_HOSTS_ENABLED=true
PRODUCTION_REQUIRE_OPENROUTER=true
PRODUCTION_REQUIRE_R2=true
PRODUCTION_REQUIRE_DATABASE=false
MAX_REQUEST_BODY_BYTES=115343360
WEB_CONCURRENCY=1
UVICORN_LIMIT_CONCURRENCY=32
UVICORN_BACKLOG=128
UVICORN_TIMEOUT_KEEP_ALIVE=10
UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30
FORWARDED_ALLOW_IPS=*
```

Add the existing OpenRouter and primary R2 credentials. Set `PRODUCTION_REQUIRE_DATABASE=true` only after the production PostgreSQL configuration is present and tested.

## Deployment procedure

1. Replace the HIVE repository contents with the production-ready package.
2. Commit and push to the deployment branch.
3. Confirm the GitHub Actions workflow is fully green.
4. Add the production environment values and secrets in Koyeb.
5. Set the Koyeb HTTP health-check path to `/readyz`.
6. Deploy.
7. Run:

```bash
HIVE_URL=https://<service>.koyeb.app \
ADMIN_BEARER_TOKEN='<token>' \
./scripts/production_smoke.sh
```

## Frozen-file assurance

The owner-specified backend, test, and documentation files were not changed. Their SHA-256 hashes match the uploaded source archive. The absent file `docs/releases/v1.25-production-execution-gates.md` was not created or altered.
