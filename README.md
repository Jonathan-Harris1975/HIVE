# HIVE

HIVE (Harris Intelligent Virtual Entity) is the private operations backend for chat, file analysis, repository intelligence, model/provider governance, skills, workflow planning and ecosystem health. It is a Python/FastAPI service deployed on Koyeb and consumed by HIVE-UI through an authenticated Cloudflare proxy.

This README describes the current capabilities and operating contract. Runtime version values are governed by the package and configuration files.

## Architecture

```text
HIVE-UI (Cloudflare)
  -> signed operator session
  -> authenticated proxy
  -> HIVE (Koyeb / FastAPI)
      -> OpenRouter and configured compatible providers
      -> PostgreSQL / optional D1
      -> Cloudflare R2, AI Search and Vectorize
      -> GitHub and ecosystem health probes
```

## Supported runtime

`backend/pyproject.toml` declares Python `>=3.11,<3.15`. The Docker/runtime and CI files are the authority for the exact deployed/interpreted minor versions.

## Production capabilities

- Persistent streamed conversations, rename and deletion.
- Cost-aware model routing and model discovery.
- Headroom prompt compression with protected system/recent-message handling.
- Upload, extraction and bounded chat for supported documents and ZIPs.
- Read-only browsing/chat across configured ecosystem R2 lanes.
- Repository Manager: safe ZIP extraction, fingerprinting, language/dependency manifest and incremental re-indexing.
- Repository Memory: Project DNA, architecture, standards, known issues and QA/optimisation/Council history.
- Model Registry: ranked category-specific model lists and defaults.
- Provider Framework: OpenRouter plus configured OpenRouter-compatible providers with model, pricing, capability and health metadata.
- AI Council: provider discovery, catalogue refresh, model benchmarking, promotion decisions and downstream ops events.
- Benchmark Engine: weighted quality/cost/latency/reliability/context/structured-output scoring.
- Repository QA: static validation only. Uploaded repositories are not executed.
- Repository Council: nine-dimension repository review with historical tracking.
- Bucket Manager: explicit accessible/hidden R2 registry.
- Connector diagnostics for OpenRouter, R2, AI Search and GitHub.
- Optimisation Engine with confidence, previous/new state and rollback records.
- Repository Learning and Project DNA refresh.
- Environment audit against `.env.example`.
- Cloudflare Workers AI embeddings, Vectorize retrieval and AI Search fan-out.
- Skills discovery, integrity checks, workflow planning, review queues and approved adapter hand-off.
- Authenticated ecosystem health aggregation for HIVE-UI Ops.

## Important endpoints

| Endpoint | Purpose |
|---|---|
| `GET /livez` | process liveness |
| `GET /readyz` | deployment readiness without secrets |
| `GET /v1/runtime/readiness` | authenticated dependency/configuration readiness |
| `GET /v1/system/repo-health` | ecosystem health snapshot |
| `GET /v1/models` | model catalogue/groups |
| `POST /v1/repositories` | register a repository ZIP |
| `GET /v1/repositories/{id}/memory` | Repository Memory |
| `POST /v1/repositories/{id}/qa` | static Repository QA |
| `POST /v1/repositories/{id}/council` | Repository Council |
| `GET /v1/model-registry/{category}` | ranked model category |
| `GET /v1/providers` | provider discovery |
| `GET /v1/providers/health` | provider health |
| `POST /v1/ai-council/run` | model/provider Council run |
| `POST /v1/benchmark/rank` | ad-hoc model ranking |
| `GET /v1/buckets` | accessible bucket registry |
| `GET /v1/connectors` | connector diagnostics |
| `POST /v1/optimisation/decisions` | record reversible optimisation decision |
| `GET /v1/environment/audit` | environment drift audit |

Unless explicitly documented otherwise, `/v1/*` routes require `Authorization: Bearer <ADMIN_BEARER_TOKEN>`.

## Configuration split

Non-secret production defaults live in `HIVE-PRODUCTION-SHARED.env`. Secret-backed variables belong in Koyeb and are documented in `HIVE-KOYEB-SECRETS-ONLY.env`. Existing platform variables override shared defaults.

There is currently **configuration version drift** between repository files: `.env.example` advertises a newer `APP_VERSION` than `HIVE-PRODUCTION-SHARED.env`/some runtime references. That should be normalised before using the version string as production evidence. Functional capability should be verified through tests/endpoints rather than a README marker.

## Local verification

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pip check
PYTHONPATH=backend APP_ENV=test python -m compileall -q backend/app backend/tests scripts
PYTHONPATH=backend APP_ENV=test python -m pytest backend/tests -q --tb=short
PYTHONPATH=backend python -m ruff check backend/app backend/tests scripts --select E4,E7,E9,F
PYTHONPATH=backend python -m mypy backend/app --no-incremental --show-error-codes
python -m bandit -q -r backend/app -ll
python -m pip_audit -r requirements.txt
```

Run locally with:

```bash
PYTHONPATH=backend uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Deployment and security

Use the root `Dockerfile` on Koyeb and the readiness/liveness endpoints above. Keep provider/storage credentials server-side, enforce production CORS/trusted-host policy, preserve upload/extraction limits and never expose hidden R2 lanes through ordinary browsing workflows.

See `SECURITY.md`, `docs/OPERATIONS.md`, `docs/koyeb-deployment.md`, `docs/model-policy.md` and `CONTRIBUTING.md`.
