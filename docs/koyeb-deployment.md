# Koyeb deployment

HIVE now includes two deployment paths so Koyeb can deploy it without guessing.

## Preferred path: Dockerfile

Koyeb should detect the root-level `Dockerfile` and run the FastAPI backend using:

```bash
/app/scripts/start.sh
```

The container exposes port `8080`, but the start script honours Koyeb's `PORT` environment variable when provided.

## Fallback path: buildpack / Nixpacks

The repo also includes:

- `requirements.txt`
- `runtime.txt`
- `Procfile`
- `nixpacks.toml`
- `scripts/start.sh`

If Docker is not selected, the buildpack/Nixpacks route installs the root `requirements.txt` and starts the backend with:

```bash
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --app-dir backend
```

## Minimum production environment variables

```env
APP_ENV=production
ADMIN_BEARER_TOKEN=replace-with-long-random-token
CORS_ORIGINS=https://your-frontend-domain.example

OPENROUTER_API_KEY=sk-or-...
OPENROUTER_SITE_URL=https://your-koyeb-service.example
OPENROUTER_APP_TITLE=HIVE
OPENROUTER_EMPTY_REPLY_RETRY_ENABLED=true
OPENROUTER_MIN_RESPONSE_TOKENS=80

CF_R2_ACCOUNT_ID=
CF_R2_ACCESS_KEY_ID=
CF_R2_SECRET_ACCESS_KEY=
CF_R2_BUCKET=ops-chat-uploads
CF_R2_PUBLIC_BASE_URL=
```

Leave D1, Vectorize and Redis-style services disabled/unconfigured for the first smoke test. Bring them in after `/health`, `/v1/models`, and basic chat are behaving.

## First checks

```bash
curl -fsS https://your-koyeb-service.example/health
curl -fsS -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  https://your-koyeb-service.example/v1/models
```


## R2 text upload smoke test

Once `/health` shows `"r2_configured": true`, test R2 without multipart file upload using JSON:

```bash
curl -X POST "https://your-koyeb-service.example/v1/files/upload-text" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"hive-r2-smoke.txt\",\"content\":\"HIVE R2 smoke test. Upload pipeline working.\"}"
```

A successful R2 response should include:

```json
{
  "ok": true,
  "file": {
    "storage": "r2",
    "object_key": "uploads/.../hive-r2-smoke.txt",
    "supported_for_text": true,
    "chunk_count": 1
  }
}
```

The original multipart endpoint remains available at `POST /v1/files/upload` for real file uploads.

## Notes

- Do not expose the OpenRouter key to the browser.
- Keep uploads routed through the backend so ZIP safety checks and R2 storage happen server-side.
- Use Docker for the most predictable Koyeb deployment. The buildpack files are there as a backup route, not the main road.


## R2 list/read/chat smoke tests

After a successful `/v1/files/upload-text` response, copy the returned `object_key`.

List recent uploaded objects:

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/list?prefix=uploads/&limit=20" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Read a stored text object:

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/read?key=uploads/FILE_ID/hive-r2-smoke.txt" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Ask HIVE about a stored object:

```bash
curl -X POST "https://your-koyeb-service.example/v1/chat/with-file" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"object_key\":\"uploads/FILE_ID/hive-r2-smoke.txt\",\"message\":\"What does this file confirm?\",\"mode\":\"file_analysis\",\"model\":\"nvidia/nemotron-3-ultra-550b-a55b:free\",\"max_tokens\":400}"
```

For v1 this route reads up to `MAX_FILE_READ_BYTES` and injects up to `MAX_FILE_CHAT_CHARS` into the model context. Keep large-file RAG for the Vectorize phase.

R2 runtime guardrails:

```env
R2_CONNECT_TIMEOUT_SECONDS=8
R2_READ_TIMEOUT_SECONDS=20
R2_MAX_ATTEMPTS=2
R2_ADDRESSING_STYLE=path
```

If `/v1/files/list`, `/v1/files/read`, or `/v1/chat/with-file` fails, call `/v1/files/diagnostics?prefix=uploads/` first. It returns safe JSON diagnostics instead of hiding the problem behind a generic 502.


## Final v1 smoke-test notes

ReqBin and similar remote curl tools can timeout on slow free models or streaming calls. Prefer these settings for v1 smoke tests:

```env
OPENROUTER_ATTEMPT_TIMEOUT_SECONDS=30
OPENROUTER_EMPTY_REPLY_RETRY_ENABLED=true
OPENROUTER_MIN_RESPONSE_TOKENS=80
ALLOW_PAID_FALLBACK=false
```

For direct `/v1/chat` tests, avoid very small `max_tokens` values. If a model still returns no visible text, HIVE returns `ok:false` with `error_code:"empty_model_reply"` instead of `ok:true` and `reply:null`.

## Optional Koyeb PostgreSQL persistence

For operational HIVE history, enable the SQL layer only after the core v1 smoke tests are passing.

```env
DATABASE_ENABLED=true
DATABASE_HOST=your-koyeb-postgres-host
DATABASE_PORT=5432
DATABASE_USER=your-database-user
DATABASE_PASSWORD=your-database-password
DATABASE_NAME=your-database-name
DATABASE_SSLMODE=require
DATABASE_CONNECT_TIMEOUT_SECONDS=8
DATABASE_STATEMENT_TIMEOUT_SECONDS=30
```

Then redeploy and run:

```bash
curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/diagnostics" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"

curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/init" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

If the password is missing, diagnostics will show the SQL store as configured but the probe will fail. Add the missing secret, redeploy, then rerun `/v1/db/init`.


## File-chat timeout diagnostics

`/v1/chat/with-file` supports `dry_run:true` / `skip_model:true` to verify R2 read and prompt construction without a model call. Configure the model-call guard with:

```env
CHAT_WITH_FILE_MODEL_TIMEOUT_SECONDS=30
```

The endpoint returns `stage`, `timings`, and `error_code:"chat_with_file_timeout"` instead of a hanging request when model calls exceed the guard.


### Production persistence verification

After enabling Koyeb PostgreSQL and Cloudflare D1, run these in order:

```bash
curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/init" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"

curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/ping-write" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"

curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/diagnostics" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

`/v1/db/ping-write` writes and deletes temporary probe rows in SQL and D1. It is the quick check that the persistence layer is not stuck in an aborted PostgreSQL transaction and that D1 writes are accepted.


## Chunk retrieval env controls

These are safe defaults for the v1.2 SQL chunking foundation:

```env
FILE_CHUNK_MAX_CHARS=4000
FILE_CHUNK_OVERLAP_CHARS=400
FILE_CHUNK_MAX_COUNT=500
FILE_RETRIEVAL_MAX_CHUNKS=6
```

Run `/v1/db/init` after deploying this version so Koyeb PostgreSQL creates the `hive_file_chunks` table.


## Optional Vectorize v1.3 env controls

Keep these disabled until the Vectorize index exists and `/v1/vectorize/diagnostics` passes. The token should have Cloudflare Vectorize read/write permissions.

```env
VECTORIZE_ENABLED=false
VECTORIZE_ACCOUNT_ID=3fb60a7136e950a7ec74959b45e4635e
VECTORIZE_API_TOKEN={{ secret.Vectorize_API_kEY }}
VECTORIZE_INDEX_NAME=hive-chunks
VECTORIZE_TIMEOUT_SECONDS=15
VECTORIZE_MAX_ATTEMPTS=2
VECTORIZE_TOP_K=8
VECTORIZE_RETURN_METADATA=all

EMBEDDINGS_ENABLED=false
EMBEDDINGS_PROVIDER=cloudflare
EMBEDDINGS_MODEL=@cf/baai/bge-base-en-v1.5
EMBEDDINGS_DIMENSIONS=768
EMBEDDINGS_TIMEOUT_SECONDS=20
EMBEDDINGS_MAX_BATCH_SIZE=32
```

After confirming diagnostics, switch `VECTORIZE_ENABLED=true` and `EMBEDDINGS_ENABLED=true`, then run `/v1/files/vectorize` for already chunked files.


## v1.4 deployment checks

After deploying v1.4, verify:

```bash
curl https://YOUR-KOYEB-APP.koyeb.app/health
curl https://YOUR-KOYEB-APP.koyeb.app/healthz
```

`/health` should show `build: v1.17-registry-integrity` and clean flags for R2, SQL, D1, Vectorize and embeddings. `/healthz` is deliberately small and unauthenticated for later MAST keep-awake use.

Use `POST /v1/db/test-cleanup` with `dry_run:true` before deleting smoke-test records.

If a token is pasted into a browser, chat, log, or screenshot, rotate it in Cloudflare/OpenRouter, update the Koyeb secret, then redeploy.

## v1.5 Koyeb free-tier settings

For the current free Koyeb web service, keep extraction bounded:

```env
HIVE_FREE_TIER_MODE=true
DOCUMENT_EXTRACT_MAX_CHARS=120000
DOCUMENT_EXTRACT_PDF_MAX_PAGES=40
DOCUMENT_EXTRACT_XLSX_MAX_ROWS_PER_SHEET=500
DOCUMENT_EXTRACT_XLSX_MAX_SHEETS=12
ZIP_EXTRACT_MAX_MEMBERS=80
ZIP_EXTRACT_MAX_MEMBER_BYTES=2097152
ZIP_EXTRACT_MAX_TOTAL_TEXT_CHARS=120000
ZIP_EXTRACT_MAX_DEPTH=2
```

MAST can ping `GET /healthz` every 10 minutes using `HIVE_KEEPAWAKE_URL` to reduce free-service cold starts. Keep this endpoint unauthenticated and minimal.

## v1.6 deployment checks

After deploying v1.6, verify:

```bash
curl "$HIVE_URL/health"
curl "$HIVE_URL/healthz"
curl "$HIVE_URL/v1/workflow-presets" -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
curl "$HIVE_URL/v1/files/r2-lanes" -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

`/health` should show `build: v1.17-registry-integrity`, `workflow_presets_enabled: true`, and `r2_ecosystem_lanes_enabled: true`.

Keep MAST keep-awake pings gentle on Koyeb Free. Use `/healthz`, not authenticated file/chat endpoints.

## v1.8 Skill Registry Import Env

Optional tuning for the R2 shared skill-pool importer:

```env
SKILL_REGISTRY_IMPORT_MAX_ITEMS=250
SKILL_REGISTRY_IMPORT_TIMEOUT_SECONDS=20
```

The importer uses `R2_PUBLIC_BASE_URL_HIVE_SKILLS` and reads `index/search-documents.json`. Run it as a manual endpoint, not a background process, on Koyeb Free.

## v1.9 Intelligent Skill Search Checks

After deploy, `/health` should show `build: v1.17-registry-integrity`.

Useful checks:

```bash
curl "$HIVE_URL/v1/skills/search?q=RSS%20rewrite&limit=10" -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
curl "$HIVE_URL/v1/skills/by-repo?repo=AIMS&limit=10" -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
curl "$HIVE_URL/v1/skills/by-risk?risk=high&limit=10" -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

The search layer is bounded and free-tier safe because it reads the imported D1 catalogue rather than walking R2 buckets.

## v1.17 registry integrity smoke checks

After deploying `v1.17-registry-integrity`, run:

```bash
curl "$HIVE_URL/health"

curl "$HIVE_URL/v1/skills/integrity?limit=500" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"

curl -X POST "$HIVE_URL/v1/skills/rebuild-index" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dry_run":true}'
```

For script-based checks:

```bash
ADMIN_BEARER_TOKEN=... python scripts/v117_registry_integrity_smoke.py
```

Keep live rebuilds explicit. The endpoint is safe for Koyeb Free because it performs one bounded manifest import and does not run background jobs.
