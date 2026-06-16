> **Document status:** Production reference  
> **Last reviewed:** 16 June 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# HIVE backend production readiness

This hardening layer keeps the existing HIVE API contract intact while tightening deployment, configuration, observability, and container behaviour.

## Mandatory Koyeb settings

```env
APP_ENV=production
APP_VERSION=1.23.1-production
ADMIN_BEARER_TOKEN=<unique random value, at least 32 characters>
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

Add the existing OpenRouter and R2 secrets. Set `PRODUCTION_REQUIRE_DATABASE=true` once PostgreSQL persistence is provisioned and verified.

## Health model

- `GET /livez`: process liveness only.
- `GET /readyz`: safe, unauthenticated configuration readiness summary.
- `GET /v1/runtime/readiness`: authenticated detailed configuration report with no secret values.
- Existing `/health` and `/healthz` routes remain unchanged for compatibility.

Configure Koyeb's HTTP health check to use `/readyz`. The Docker image's internal health check uses `/livez`.

## Deployment gate

Run before deployment with the same environment values used by Koyeb:

```bash
python scripts/production_preflight.py --allow-warnings
```

Run after deployment:

```bash
HIVE_URL=https://<service>.koyeb.app \
ADMIN_BEARER_TOKEN='<token>' \
./scripts/production_smoke.sh
```

## Container behaviour

The production image:

- builds dependencies in a separate stage;
- runs as the unprivileged `hive` user;
- exposes no Uvicorn server banner;
- uses one worker by default for the Koyeb eco-micro footprint;
- applies concurrency, backlog, keep-alive, and graceful-shutdown limits;
- includes request IDs, bounded request bodies, API security headers, and safe request completion logs.

## Dependency maintenance

`requirements.in` contains the reviewed direct versions. `requirements.txt` and `requirements.lock` contain the compiled runtime set. Regenerate them deliberately after testing, then let CI run unit tests and `pip-audit`.
