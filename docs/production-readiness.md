> **Document status:** Production reference  
> **Last reviewed:** 20 September 2026  
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
ALLOWED_HOSTS=hive.jonathan-harris.online,liable-loreen-jonathanharris-57884580.koyeb.app
API_DOCS_ENABLED=false
SECURITY_HEADERS_ENABLED=true
REQUEST_LOGGING_ENABLED=true
TRUSTED_HOSTS_ENABLED=true
PRODUCTION_REQUIRE_OPENROUTER=true
PRODUCTION_REQUIRE_R2=true
PRODUCTION_REQUIRE_DATABASE=true
MAX_REQUEST_BODY_BYTES=146800640
MODEL_REGISTRY_PENDING_R2_LANE=meta_system
MODEL_REGISTRY_PENDING_R2_PREFIX=state/hive/model-registry-pending
MODEL_REGISTRY_PENDING_MAX_BYTES=1048576
MODEL_REGISTRY_PENDING_MAX_OPERATIONS=5000
MODEL_REGISTRY_PENDING_LEASE_SECONDS=120
WEB_CONCURRENCY=1
UVICORN_LIMIT_CONCURRENCY=32
UVICORN_BACKLOG=128
UVICORN_TIMEOUT_KEEP_ALIVE=10
UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30
FORWARDED_ALLOW_IPS=127.0.0.1
REPOSITORY_BULK_MAX_COUNT=8
REPOSITORY_BULK_MAX_TOTAL_BYTES=838860800
REPOSITORY_BULK_CONCURRENCY=2
REPOSITORY_IMPROVEMENT_MAX_CHANGE_RATIO=0.12
REPOSITORY_IMPROVEMENT_MAX_CHANGE_FILES=100
REPOSITORY_IMPROVEMENT_MAX_WORK_PASSES=4
```

Add the existing OpenRouter, R2 and production database secrets. Production now requires durable database persistence: `PRODUCTION_REQUIRE_DATABASE=true` is authoritative and deployment readiness must fail closed if the configured database cannot be verified. The development example may keep this flag disabled for local work, but production must not override it to `false`.

Production `ALLOWED_HOSTS` accepts exact hostnames only. Provider-wide patterns such as `*.koyeb.app` and the global `*` wildcard fail the production preflight. The committed Koyeb hostname is the exact HIVE service hostname already used by the repository's production smoke scripts.

Model Registry mutations remain available during a temporary D1 outage only when the intended operation has first been written to the private R2 lane named by `MODEL_REGISTRY_PENDING_R2_LANE`. The `meta_system` bucket and write credentials are mandatory when production D1 is enabled. Pending objects are stored beneath `MODEL_REGISTRY_PENDING_R2_PREFIX`, surfaced as `persistence_state=pending`, loaded by a fresh instance with no prior filesystem, and retried automatically at startup or through authenticated `POST /v1/model-registry/reconcile`.

If D1 and the R2 fallback both fail, the mutation is rejected with a service-unavailable response and is not exposed in memory. Per-operation conditional R2 claims prevent concurrent instances from intentionally reconciling the same operation; D1 upsert/delete semantics provide an additional idempotency layer. Newer operations for one model supersede stale registration/deletion intents, and R2 work is removed only after D1 accepts the matching operation. Container-local disk is not part of this durability contract.

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
- is scanned after build/boot for fixable High/Critical OS and library vulnerabilities, with a retained human-readable report.

The automatic `main` deployment watcher also fails closed when `KOYEB_TOKEN` or `KOYEB_SERVICE` is missing. It retains an exact-SHA production attestation only after the bounded Koyeb watcher succeeds; alert delivery does not weaken that result.

## Autonomy dispatch, incident durability, and owner verification

Repository code cannot verify the current Koyeb deployment settings, GitHub environment protection, Kilo webhook authorisation, or an external OIDC provider's trust policy. Keep the HIVE controller in reviewable, exact-SHA operation until the service owners complete the following checks.

### Owner configuration checks

- **Koyeb:** set the production HTTP health check to `/readyz`; verify the production variables and secret names in the Koyeb dashboard against `HIVE-PRODUCTION-SHARED.env` and `HIVE-KOYEB-SECRETS-ONLY.env`. Confirm the deployed revision matches the approved source SHA. Do not paste values into an issue, report, or repository file.
- **GitHub:** verify the `main` ruleset, required checks, environment reviewers, and the autonomous repair workflow's effective token permissions. The workflow must be able to retain its run-correlated issue comment, but must not receive broader write permissions to make Kilo succeed.
- **Circuit breaker:** add repository variable `HIVE_REPAIR_CIRCUIT_BREAKER` with value `open` only after the Kilo 403 and queued incidents have been reconciled. Missing or any value other than `open` pauses automatic dispatch and records a `circuit-open` marker. Set it to `closed` for an emergency stop. Reopening permits only a current, exact-SHA incident with an explicit non-acceptance state; accepted, pending, and uncertain deliveries remain stopped.
- **Kilo:** investigate the historical HTTP 403 from run `39179584274`. Restore the intended webhook URL/authorisation through the service owner, then run one controlled non-production handoff. Verify a 2xx response, an `accepted` marker carrying the run ID and source SHA, one Kilo task, and no duplicate task on a replay of the same event. The repository keeps a bounded two-attempt budget for explicit 4xx rejection; timeouts and 5xx responses are recorded as uncertain and require owner reconciliation before replay.
- **Operational event store and alert route:** verify D1 is enabled and readable/writable in production, then deliver a test event with a stable event ID. Confirm it is visible after a fresh service instance starts. Verify the webhook endpoint accepts the same ID idempotently, transient 429/5xx or transport failures receive at most three identical delivery attempts, and a final failure remains a failed workflow with a run-linked alert for follow-up.
- **OIDC federation, where a cloud/service provider supports it:** configure the external trust policy with issuer `https://token.actions.githubusercontent.com`, audience `https://github.com/Jonathan-Harris1975`, repository `Jonathan-Harris1975/HIVE` (repository ID `1262518964`, owner ID `205491236`), default branch `refs/heads/main`, and workflow reference `Jonathan-Harris1975/HIVE/.github/workflows/oidc-readiness.yml@refs/heads/main`. Bind the subject to the exact value the repository emits: either `repo:Jonathan-Harris1975/HIVE:ref:refs/heads/main` or the configured immutable subject template; do not allow both by default. Where supported, also require the repository and workflow claims as separate trust conditions. Grant only the intended role and audience. The OIDC readiness workflow verifies the GitHub token itself; it does **not** prove that any external provider trusts it or that the provider role is least-privileged.
- **Cloudflare account security:** resolve the audit's disabled account-level 2FA enforcement finding and independently verify user MFA. Repository changes cannot alter or verify this account setting.

### Exact-revision live test after owner configuration

1. Review and approve the implementation branch, then record the merged commit SHA. Confirm its CI and security checks passed before any deployment.
2. Deploy only that SHA through the existing protected release path. Confirm Koyeb reports that exact revision and `/readyz` returns ready.
3. From a non-production test event, verify the dispatch lifecycle: one failure produces one durable marker with exact run ID/SHA; explicit 403 becomes a visible rejected incident; a timeout or 5xx becomes uncertain and stops replay; one replay after an owner-confirmed rejection creates no duplicate task; and two explicit rejections exhaust the automatic budget.
4. Temporarily make the test D1 write/read unavailable in a non-production environment. Confirm ingestion returns retryable 503 rather than 202, the sender retries the same event ID no more than twice after the first attempt, the inbox reports unavailable rather than empty, and successful replay makes exactly one durable incident visible.
5. Verify the provider trust test separately from the GitHub JWT readiness artifact. Record issuer, audience, exact subject/workflow restriction, role, source SHA, and result without recording token contents.
6. Confirm operator notification receipt and close the test incident only after the durable event and the provider's final state agree. Retain evidence and the owner who reconciled uncertain delivery.

These tests establish implementation behaviour only for the tested revision and environment. They do not establish broad production readiness, unattended recovery, or successful recovery of the historical 403 until the owner-run checks pass.

## Dependency maintenance

`requirements.in` contains the reviewed direct versions and `requirements.txt` is the compiled runtime set. Regenerate it with `python -m piptools compile --generate-hashes --output-file=requirements.txt --strip-extras requirements.in`, then run `python scripts/verify_dependency_lock.py --compile`, `pip check`, the full Python 3.11-3.14 test matrix, `pip-audit`, and the Docker runtime smoke gate. The September 2026 refresh uses Uvicorn 0.53.0, pypdf 6.19.0, pydantic-settings 2.15.0, and boto3/botocore 1.43.98; the compiled Pydantic resolution remains 2.13.5 / pydantic-core 2.46.5. Settings-source matching is explicitly case-insensitive to preserve HIVE's environment-alias contract with pydantic-settings 2.15. The previous pypdf 6.16.1 pin was already patched for the August 2026 XForm resource-consumption advisory. The repository does not maintain a second `requirements.lock` file.

Production repository readiness also verifies the complete canonical eight-repository GitHub catalogue when governed refresh is enabled, bulk-ingestion capacity for all eight repositories, and a separate per-pass improvement scope no greater than 12%. See [`repository-management.md`](repository-management.md).

## Production environment split

Non-secret production defaults are committed in `HIVE-PRODUCTION-SHARED.env`. Koyeb should keep only the secret-backed values listed in `HIVE-KOYEB-SECRETS-ONLY.env`; runtime env values override the shared file.
