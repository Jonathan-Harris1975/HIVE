> **Document status:** Production reference  
> **Last reviewed:** 22 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# HIVE

**Current build marker:** `v1.26.12-catalogue-metadata` / `APP_VERSION=1.26.12-production`.

HIVE (Harris Intelligent Virtual Entity) is the private operations backend for chat, file analysis, repository intelligence, skills, workflow planning and ecosystem health. It is a Python/FastAPI service deployed on Koyeb and consumed by HIVE-UI through an authenticated Cloudflare Pages proxy.

## Production architecture

```text
HIVE-UI (Cloudflare Pages)
  -> signed operator session
  -> Cloudflare Pages API proxy
  -> HIVE (Koyeb/FastAPI)
      -> OpenRouter
      -> PostgreSQL and optional D1
      -> Cloudflare R2 and Vectorize
      -> ecosystem health probes
```

## Supported production capabilities

- Persistent streamed conversations with rename and deletion.
- Cost-aware model routing and grouped model discovery.
- Upload, extraction and bounded file chat for supported documents and ZIPs.
- Read-only browsing and chat across configured ecosystem R2 buckets.
- Repository Manager: safe ZIP extraction, fingerprinting, manifest generation (language + dependency detection), incremental re-indexing and TTL-based cleanup of uploaded repositories.
- Repository Memory: persistent Project DNA, architecture, coding standards, build/deployment profiles, known issues, learned patterns, patch/optimisation/QA/Council history per repository, queryable via Cloudflare AI Search without reloading the repository.
- Model Registry: dynamic, ranked models per category (coding, reasoning, planning, vision, research, fast, cheap, creative, long context); the highest-ranked coding model automatically becomes the default coding model.
- Provider Framework: OpenRouter plus any configured OpenRouter-compatible provider, each exposing available models, pricing, context length, tool/structured-output support, and health/latency through one adapter shape.
- AI Council: on-demand run that discovers providers, refreshes catalogues, detects new/retired models, scores coding-capable models with the Benchmark Engine, auto-promotes those above a configurable threshold into the Model Registry, and notifies downstream services via the ops-event inbox.
- Benchmark Engine: configurable weighted scoring across coding/reasoning benchmarks, cost, latency, reliability, long-context, JSON reliability, structured output, community maturity, and internal historical performance.
- Cloudflare Workers AI embeddings and Vectorize retrieval.
- Skills search, integrity checks and review-gated workflow planning.
- Repository hygiene, execution previews, evidence packs, review queues and approved production adapter handoff.
- Authenticated ecosystem health aggregation for HIVE-UI Ops.

## Health and operator endpoints

| Endpoint | Auth | Purpose |
|---|---:|---|
| `GET /livez` | No | Process liveness for Koyeb |
| `GET /readyz` | No | Deployment readiness without secrets |
| `GET /v1/runtime/readiness` | Bearer | Detailed runtime readiness |
| `GET /v1/system/repo-health` | Bearer | Cached ecosystem health |
| `GET /v1/models` | Bearer | OpenRouter model catalogue and groups |
| `GET /v1/files/r2-lanes` | Bearer | Configured storage lanes |
| `POST /v1/repositories` | Bearer | Upload and register a repository ZIP (Repository Manager) |
| `GET /v1/repositories` | Bearer | List registered repositories |
| `GET /v1/repositories/{id}` | Bearer | Repository manifest (fingerprint, languages, dependencies) |
| `GET /v1/repositories/{id}/memory` | Bearer | Repository Memory (Project DNA, architecture, QA/optimisation history, etc.) |
| `GET /v1/repository-memory/ai-search` | Bearer | Cloudflare AI Search query across Repository Memory |
| `GET /v1/model-registry/{category}` | Bearer | Ranked models and default for a Model Registry category |
| `GET /v1/providers` | Bearer | Discovered providers (Provider Framework) |
| `GET /v1/providers/health` | Bearer | Latency/model-count health check per provider |
| `POST /v1/ai-council/run` | Bearer | Run discovery/benchmark/promotion across all providers |
| `GET /v1/ai-council/history` | Bearer | Past AI Council run reports |
| `POST /v1/benchmark/rank` | Bearer | Ad-hoc weighted ranking of arbitrary model metrics |

All `/v1/*` routes require `Authorization: Bearer <ADMIN_BEARER_TOKEN>` unless explicitly documented otherwise.

## Local verification

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
PYTHONPATH=backend python -m pytest backend/tests -q
python -m ruff check backend/app backend/tests scripts
```

Run locally:

```bash
PYTHONPATH=backend uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Production environment split

HIVE now keeps non-secret production defaults in `HIVE-PRODUCTION-SHARED.env` in the repository. Koyeb should only hold secret-backed variables from `HIVE-KOYEB-SECRETS-ONLY.env`. The startup script loads the shared env file without overriding existing Koyeb variables, so secrets and emergency platform overrides still win.

## Deployment

Use the root `Dockerfile` on Koyeb. Keep one worker unless persistence and concurrency have been deliberately re-profiled. Configure `/readyz` as the readiness check and `/livez` as the liveness probe. Production configuration is described in [`docs/koyeb-deployment.md`](docs/koyeb-deployment.md).

## Security boundaries

- OpenRouter and storage credentials remain server-side.
- The primary `hive` upload bucket may be read/write; all other ecosystem lanes are read-only.
- Upload, ZIP and extraction limits are enforced before content reaches model context.
- Production CORS and trusted hosts are fail-closed.
- Operational responses redact tokens, credentials and unrestricted remote URLs.

See [`SECURITY.md`](SECURITY.md), [`docs/architecture.md`](docs/architecture.md) and [`docs/production-readiness.md`](docs/production-readiness.md).

## Operational event inbox

HIVE receives redacted GitHub, Koyeb, Cloudflare Pages and runtime failure events through a dedicated bearer-protected endpoint. HIVE-UI displays them on `/ops`. See [`docs/OPERATIONAL_ALERTING.md`](docs/OPERATIONAL_ALERTING.md).

## Paired skills integration contract

`backend/app/api/chat.py` and `backend/app/services/skill_registry.py` are a release pair. The chat route imports `build_skill_context`, which performs bounded skill recommendation, provenance retention and untrusted-reference prompt construction. CI imports the production app and executes the real builder to prevent partial-file deployments.

## Operational alerting

GitHub, Koyeb, Cloudflare Pages and runtime services can submit redacted events to the authenticated `/v1/ops/events` contract. See [`docs/OPERATIONAL_ALERTING.md`](docs/OPERATIONAL_ALERTING.md).
