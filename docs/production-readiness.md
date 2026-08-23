> **Document status:** Production reference  
> **Last reviewed:** 23 August 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

# HIVE backend production readiness

This hardening layer keeps the existing HIVE API contract intact while tightening deployment, configuration, observability, and container behaviour.

**Current production marker:** `APP_VERSION=1.31-production`; backend health marker `v1.31-production-readiness`.

## Mandatory Koyeb settings

```env
APP_ENV=production
APP_VERSION=1.31-production
ADMIN_BEARER_TOKEN=<unique random value, at least 32 characters>
CORS_ORIGINS=https://<your-hive-ui-domain>
ALLOWED_HOSTS=<your-service>.koyeb.app
API_DOCS_ENABLED=false
SECURITY_HEADERS_ENABLED=true
REQUEST_LOGGING_ENABLED=true
TRUSTED_HOSTS_ENABLED=true
PRODUCTION_REQUIRE_OPENROUTER=true
PRODUCTION_REQUIRE_R2=true
PRODUCTION_REQUIRE_DATABASE=true
MAX_REQUEST_BODY_BYTES=146800640
WEB_CONCURRENCY=1
UVICORN_LIMIT_CONCURRENCY=32
UVICORN_BACKLOG=128
UVICORN_TIMEOUT_KEEP_ALIVE=10
UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30
FORWARDED_ALLOW_IPS=127.0.0.1
```

Add the existing OpenRouter, R2 and production database secrets. Production now requires durable database persistence: `PRODUCTION_REQUIRE_DATABASE=true` is authoritative and deployment readiness must fail closed if the configured database cannot be verified. The development example may keep this flag disabled for local work, but production must not override it to `false`.

`FORWARDED_ALLOW_IPS` deliberately uses Uvicorn's loopback-only trust boundary. HIVE's authentication limiter handles Koyeb separately: when `KOYEB_PUBLIC_DOMAIN` is present, it validates and uses only the final `X-Forwarded-For` address, which Koyeb documents as the certified connecting client IP.

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
- uses one worker by default for the Koyeb e-medium/eco-medium footprint;
- applies concurrency, backlog, keep-alive, and graceful-shutdown limits;
- includes request IDs, bounded request bodies, API security headers, and safe request completion logs.

## Dependency maintenance

`requirements.in` contains the reviewed direct versions and `requirements.txt` is the compiled runtime set. Regenerate `requirements.txt` with `pip-compile` after deliberate dependency changes, run `pip check`, and let CI run the full Python 3.11-3.14 test matrix plus `pip-audit`. The repository does not maintain a second `requirements.lock` file.

## Production environment split

Non-secret production defaults are committed in `HIVE-PRODUCTION-SHARED.env`. Koyeb should keep only the secret-backed values listed in `HIVE-KOYEB-SECRETS-ONLY.env`; runtime env values override the shared file.
