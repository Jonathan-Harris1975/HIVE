# HIVE

HIVE is the FastAPI operator and repository-intelligence service for the wider estate. HIVE-UI reaches it through an authenticated proxy; HIVE then coordinates model/provider access, repository analysis, durable operational state, Cloudflare storage/search services, and ecosystem health data. It is not a public anonymous API.

## Architecture

```text
HIVE-UI (Cloudflare)
  -> signed operator session / authenticated proxy
  -> HIVE (Koyeb / FastAPI)
      -> OpenRouter and configured compatible providers
      -> PostgreSQL operational persistence / optional Cloudflare D1 metadata
      -> Cloudflare R2 object storage and governed multi-bucket reads
      -> Workers AI embeddings -> Vectorize semantic retrieval
      -> Cloudflare AI Search
      -> GitHub and ecosystem health probes
      -> model registry, repository memory/QA/council and reconciliation services
```

The FastAPI application is assembled under `backend/app`. API routers live in `backend/app/api`, provider/domain services in `backend/app/services`, ingestion logic in `backend/app/ingestion`, and storage adapters in `backend/app/storage`. Repository and model-registry state is persisted where configured; model-registry writes that cannot be durably committed are recorded for reconciliation rather than silently discarded.

## Supported runtime and dependency lock

The Docker/runtime and CI files are authoritative for supported Python versions. Direct production dependencies live in `requirements.in`; `requirements.txt` is the compiled runtime lock. Do not hand-edit generated transitive pins. After changing a direct dependency, regenerate with:

```bash
python -m piptools compile --output-file=requirements.txt --strip-extras requirements.in
python scripts/verify_dependency_lock.py --compile
```

The focused September 2026 dependency refresh uses Uvicorn `0.53.0`, pypdf `6.19.0`, `pydantic-settings 2.15.0`, and `boto3`/`botocore 1.43.98`; the compiled Pydantic resolution remains `2.13.5` / `pydantic-core 2.46.5`. HIVE explicitly keeps settings-source matching case-insensitive, matching its existing environment-alias contract under the pydantic-settings 2.15 source-handling changes. The earlier pypdf `6.16.1` pin was already on the patched side of the August 2026 XForm resource-consumption advisory; that advisory was not active in HIVE's prior lock.

## Core production capabilities

- Persistent streamed conversations, rename and deletion.
- Cost-aware model routing, provider discovery, model registry and catalogue reconciliation.
- Headroom prompt compression with protected system/recent-message handling.
- Upload, extraction and bounded chat for supported documents and ZIPs.
- Governed R2 upload plus scoped multi-bucket browsing/read paths.
- Repository Manager, Repository Memory, static Repository QA and Repository Council.
- AI Council, Benchmark Engine, optimisation decisions and repository-learning workflows.
- Cloudflare Workers AI embeddings, Vectorize retrieval and AI Search fan-out.
- Environment/configuration audit and authenticated ecosystem health aggregation for HIVE-UI Ops.

## Configuration

Copy `.env.example` for local development. Production non-secret defaults live in `HIVE-PRODUCTION-SHARED.env`; secret-backed values belong in the deployment platform and are enumerated in `HIVE-KOYEB-SECRETS-ONLY.env`. Runtime environment variables override shared defaults.

Important configuration groups include:

- **Authentication/network:** `ADMIN_BEARER_TOKEN`, `CORS_ORIGINS`, `ALLOWED_HOSTS`, trusted-host and forwarded-proxy settings.
- **R2:** `CF_R2_ACCOUNT_ID`, `CF_R2_ACCESS_KEY_ID`, `CF_R2_SECRET_ACCESS_KEY`, `CF_R2_BUCKET`, optional read-only multi-bucket credentials, endpoint/timeouts/retry limits, and lane-specific bucket names.
- **Embeddings:** `EMBEDDINGS_ENABLED`, `EMBEDDINGS_PROVIDER`, `EMBEDDINGS_ACCOUNT_ID`, `EMBEDDINGS_API_TOKEN`, `EMBEDDINGS_MODEL`, dimensions, timeout and batch-size settings.
- **Persistence:** production database settings, optional D1, model-registry reconciliation path.
- **Retrieval/providers:** OpenRouter, Vectorize, AI Search and optional compatible-provider settings.

Never put live provider/storage/database credentials in repository files, browser-exposed variables, logs or test fixtures. Deterministic tests use mocks/stubs only.

## Health and readiness

| Endpoint | Contract |
|---|---|
| `GET /livez` | Process liveness only. It does not prove provider or persistence readiness. |
| `GET /readyz` | Safe unauthenticated deployment/configuration readiness summary with no secrets. |
| `GET /v1/runtime/readiness` | Authenticated detailed dependency/configuration readiness and redacted diagnostics. |
| `GET /health`, `GET /healthz` | Compatibility health routes retained for existing integrations. |
| `GET /v1/connectors` | Authenticated connector diagnostics for configured integrations. |
| `GET /v1/system/repo-health` | Authenticated ecosystem health snapshot. |

Optional integrations may report a degraded state without taking the process down. Production-required dependencies are enforced by the production readiness/preflight rules.

## Provider and storage failure behaviour

R2 and embeddings integrations are deliberately defensive:

- Missing R2 credentials report the connector as unconfigured rather than throwing from diagnostics.
- R2 list/read operations translate SDK/client failures into bounded domain errors; malformed list responses are rejected rather than trusted.
- Access denied, invalid credentials and transient R2/network failures make the connector unhealthy but do not expose configured access keys/secrets.
- Embeddings timeouts, connection failures, non-2xx responses, malformed JSON, unexpected response types and vector-count mismatches return a degraded result instead of crashing the request path.
- Provider response/exception text is redacted against the configured embeddings token before it is logged or returned by the adapter.
- Fatality is determined by the caller/readiness policy. Optional retrieval features degrade; dependencies marked mandatory for production can still fail readiness closed.

## Important API areas

| Endpoint | Purpose |
|---|---|
| `GET /v1/models` | Model catalogue/groups |
| `POST /v1/repositories` | Register a repository ZIP |
| `GET /v1/repositories/{id}/memory` | Repository Memory |
| `POST /v1/repositories/{id}/qa` | Static Repository QA |
| `POST /v1/repositories/{id}/council` | Repository Council |
| `GET /v1/model-registry/{category}` | Model category registry |
| `GET /v1/providers`, `GET /v1/providers/health` | Provider discovery/health |
| `POST /v1/ai-council/run` | Model/provider Council run |
| `POST /v1/benchmark/rank` | Ad-hoc model ranking |
| `GET /v1/buckets` | Accessible bucket registry |
| `POST /v1/optimisation/decisions` | Record reversible optimisation decision |
| `GET /v1/environment/audit` | Environment drift audit |

Unless explicitly documented otherwise, `/v1/*` routes require `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

## Development and testing

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
PYTHONPATH=backend APP_ENV=test python -m compileall -q backend/app backend/tests scripts
PYTHONPATH=backend APP_ENV=test python -m pytest backend/tests -q --tb=short \
  --cov=app --cov-report=term-missing --cov-fail-under=74
PYTHONPATH=backend python -m ruff check backend/app backend/tests scripts --select E4,E7,E9,F
PYTHONPATH=backend python scripts/mypy_guard.py
python -m bandit -q -r backend/app -ll
python -m pip_audit -r requirements.txt
```

Focused deterministic R2/embeddings coverage is in `backend/tests/test_r2_embeddings_regression.py`. Those tests must remain credential-free and network-free. Live-provider smoke tests, when deliberately run against a controlled environment, are separate from ordinary CI and must use deployment-managed secrets.

Run locally with:

```bash
PYTHONPATH=backend uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Deployment and security

Use the root `Dockerfile` for the production image and `/readyz` for the external deployment health check; the image uses `/livez` internally. Production must retain exact allowed-host restrictions, HTTPS/CORS policy, bounded request/upload/extraction limits, scoped storage permissions, dependency/secret scanning and server-side credential handling.

See `SECURITY.md`, `docs/OPERATIONS.md`, `docs/production-readiness.md`, `docs/koyeb-deployment.md`, `docs/cloudflare-decisions.md`, `docs/model-policy.md` and `CONTRIBUTING.md`.
