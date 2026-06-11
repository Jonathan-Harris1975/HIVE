# HIVE (Harris Intelligent Virtual Entity)

Standalone Python-first OpenRouter operations chatbot for private brand and non-brand work.

This is **not** a ChatLima/Kanari/OrChat fork. Those projects are reference architecture only. This repo is designed as a controlled, standalone codebase in the same spirit as using Aider as inspiration while building RAMS independently.

## Core goals

- OpenRouter-powered chat with server-side API key handling.
- Dynamic model retrieval from `GET https://openrouter.ai/api/v1/models`.
- Cost-aware model routing: cheap, balanced, premium, and code/audit tiers.
- Brand Mode and General Mode.
- Server-sent event streaming for long responses.
- Cloudflare R2 object storage for uploads and extracted artefacts.
- Safe upload handling for any file type, including ZIPs.
- Document ingestion for common formats.
- Cloudflare Vectorize REST integration for optional semantic chunk retrieval.
- Pluggable metadata store: SQLite for local dev, PostgreSQL/D1 adapter later.

## Current status

**Build stage:** `v1.7-ecosystem-intelligence`.

HIVE now has working OpenRouter chat/model routing, R2/local upload storage, JSON/base64 uploads, stored ZIP inspection/extraction, SQL persistence, SQL chunk retrieval, Cloudflare D1 metadata, Cloudflare Workers AI embeddings, and Cloudflare Vectorize semantic retrieval. v1.6 adds workflow presets, grounded `source_chunks[]` metadata, retrieval summaries, and an R2 ecosystem lane registry for AIMS/RAMS/website/podcast/skills buckets.

## Recommended v1 architecture

```text
Browser UI
  -> FastAPI backend
      -> OpenRouter chat/model endpoints
      -> R2 upload/read adapter
      -> Ingestion pipeline
      -> Metadata store
      -> Vector store adapter
      -> SSE streaming responses
```

## Local development

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp ../.env.example .env
uvicorn app.main:app --reload --port 8080
```


## Deployment

The repo now includes both a Docker deployment path and a buildpack/Nixpacks fallback:

- `Dockerfile` for predictable Koyeb deployment.
- `.dockerignore` to keep the image lean.
- `requirements.txt` for root-level Python dependency detection.
- `Procfile`, `runtime.txt`, and `nixpacks.toml` for buildpack/Nixpacks fallback.
- `scripts/start.sh` as the single start command.

Recommended Koyeb path: use the `Dockerfile`. See `docs/koyeb-deployment.md`.

## Useful endpoints

- `GET /health`
- `GET /healthz` - tiny unauthenticated keep-awake point for MAST.
- `GET /v1/models`
- `POST /v1/chat/stream`
- `POST /v1/files/upload`
- `POST /v1/files/upload-text`
- `POST /v1/files/upload-base64`
- `GET /v1/files/list`
- `GET /v1/files/read?key=uploads/...`
- `GET /v1/files/public-url?key=uploads/...`
- `GET /v1/files/r2-lanes`
- `GET /v1/files/r2-lanes/public-url?lane=audits&key=reports/latest.html`
- `GET /v1/files/zip/inspect?key=uploads/...`
- `POST /v1/chat/with-file`
  - Supports `dry_run:true` / `skip_model:true` for file-read and prompt-build diagnostics.
  - Supports `workflow_preset` values such as `audit_report_review`, `repo_debug_bundle`, `ci_log_analysis`, `social_content_qa`, `podcast_episode_review`, and `ebook_keyword_review`.
- `GET /v1/workflow-presets`
- `GET /v1/db/diagnostics`
- `POST /v1/db/init`
- `GET /v1/db/conversations`
- `GET /v1/db/conversations/{conversation_id}`
- `GET /v1/db/files`
- `GET /v1/db/cost-summary`
- `POST /v1/db/test-cleanup` - dry-run-first smoke-test SQL cleanup by `test_run_id` and/or object-key prefix.
- `GET /v1/vectorize/diagnostics`
- `POST /v1/files/vectorize`
- `GET /v1/files/vector-search`
- `POST /v1/db/ecosystem-metadata`
- `GET /v1/db/ecosystem-metadata`



### JSON text upload smoke test

Use this when testing from ReqBin, Make.com, or a phone where multipart `curl -F` uploads are awkward:

```bash
curl -X POST "https://your-koyeb-service.example/v1/files/upload-text" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"hive-r2-smoke.txt\",\"content\":\"HIVE R2 smoke test. Upload pipeline working.\"}"
```

Expected response includes `storage`, `object_key`, `public_url`, `chunk_count`, and `supported_for_text`.


### Base64 upload and ZIP inspect smoke tests

Use base64 uploads for ReqBin, Make.com, or phone-based tests where multipart upload is fiddly:

```bash
curl -X POST "https://your-koyeb-service.example/v1/files/upload-base64" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"sample.txt\",\"content_type\":\"text/plain\",\"content_base64\":\"SElWRSBiYXNlNjQgdXBsb2FkIHdvcmtzLg==\"}"
```

For ZIPs, upload a base64 ZIP or use multipart upload, then inspect it without extraction:

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/zip/inspect?key=uploads/FILE_ID/sample.zip" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

The ZIP inspector checks path traversal, member count and uncompressed-size limits. V1 inspects ZIP contents but does not yet extract every member into R2.

Get a public URL for a stored key:

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/public-url?key=uploads/FILE_ID/sample.txt" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

### File read and chat smoke tests

List stored files:

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/list?prefix=uploads/&limit=20" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Read a stored text object back:

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/read?key=uploads/FILE_ID/hive-r2-smoke.txt" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Ask a question about one stored file:

```bash
curl -X POST "https://your-koyeb-service.example/v1/chat/with-file" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"object_key\":\"uploads/FILE_ID/hive-r2-smoke.txt\",\"message\":\"Summarise this file in one sentence.\",\"mode\":\"file_analysis\",\"model\":\"nvidia/nemotron-3-ultra-550b-a55b:free\"}"
```

The v1 file-chat route injects a bounded text excerpt into the prompt. The model is told not to print long object keys inside the reply because the API returns `source` and `source_citation` metadata separately. If a model returns no visible assistant text, HIVE retries the fallback ladder and returns `ok:false` with `error_code:"empty_model_reply"` if all attempts remain empty. Vectorize/chunk retrieval will replace this later for larger corpora.

If hosted runners stick on file chat, run a dry-run first. This verifies R2 read + prompt construction without calling OpenRouter:

```bash
curl -X POST "https://your-koyeb-service.example/v1/chat/with-file" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"object_key\":\"uploads/FILE_ID/hive-r2-smoke.txt\",\"message\":\"What does this file confirm?\",\"dry_run\":true}"
```

Normal file-chat responses now include `stage` and `timings`. If the model call exceeds `CHAT_WITH_FILE_MODEL_TIMEOUT_SECONDS` or `model_timeout_seconds`, HIVE returns `ok:false`, `error_code:"chat_with_file_timeout"`, source metadata, and timings instead of leaving the client hanging.

### Empty reply and truncation behaviour

Some reasoning-heavy free models can spend a tiny output budget on internal reasoning and return no visible assistant text. HIVE treats that as incomplete rather than a clean success.

Responses now include:

- `completion_truncated`: `true` when the provider stops because of output length.
- `empty_reply`: `true` only when the final visible reply field is blank.
- `error_code`: `empty_model_reply` when all configured attempts returned no visible text.
- `attempts`: structured diagnostics for failed/empty model attempts.

Use at least `max_tokens:80` for ReqBin/free-model smoke tests. HIVE also enforces `OPENROUTER_MIN_RESPONSE_TOKENS` to avoid tiny token budgets producing false failures.

## Environment

See `.env.example`.

## Security stance

- OpenRouter key is never sent to the client.
- Admin bearer token required outside dev mode.
- ZIP extraction uses path traversal checks and size/file-count limits.
- Unsupported files are stored and recorded, not blindly pushed into model context.
- Uploaded content should be indexed and selected before model use to avoid token bonfires.

### R2 diagnostics

If file list/read tests return errors, use the diagnostics endpoint first. It returns safe, redacted R2 configuration plus a small list probe instead of surfacing a raw 502.

```bash
curl -X GET "https://your-koyeb-service.example/v1/files/diagnostics?prefix=uploads/" \
  -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

File list/read/chat-with-file now return `ok:false` JSON diagnostics for R2 runtime failures rather than a generic Bad Gateway.

## Optional v1.1 persistence

HIVE v1 works without a database. The optional v1.1 persistence layer adds:

- SQL store for conversations, messages, files, upload records and cost/token logs.
- Cloudflare D1 store for AIMS/RAMS ecosystem metadata indexes.

Recommended long-term split:

| Koyeb/PostgreSQL or SQLite | Cloudflare D1 |
| --- | --- |
| Conversations | Ecosystem metadata |
| Messages | Audit run index |
| File metadata | Council report index |
| Upload records | Podcast episode index |
| Cost tracking | Book/ebook catalogue cache |
| Token usage data | Social performance snapshots |

### Database smoke tests

```bash
curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/diagnostics" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"

curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/init" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

The core chat/file routes continue to work if the database layer is disabled or temporarily unavailable.



### Production-grade persistence hardening

The SQL layer uses true `ON CONFLICT` upserts for conversations and files. It does **not** use insert-fail-then-update flows, because PostgreSQL marks a transaction as aborted after a failed statement. Every SQL write runs inside a transaction context that commits on success and rolls back on failure before the connection closes.

Use this after deployment, after `/v1/db/init`, or after any persistence error:

```bash
curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/ping-write" \
  -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

Expected: SQL and D1 both return `ok:true`. The probe creates and deletes temporary diagnostic records, so it should not clutter the database.

Key production envs:

```env
DATABASE_ENABLED=true
DATABASE_SSLMODE=require
DATABASE_CONNECT_TIMEOUT_SECONDS=8
DATABASE_STATEMENT_TIMEOUT_SECONDS=30
D1_ENABLED=true
D1_TIMEOUT_SECONDS=12
D1_MAX_ATTEMPTS=2
```

### Persistence retrieval and conversation resume

Once `DATABASE_ENABLED=true` and `/v1/db/init` has run, HIVE automatically records non-streaming `/v1/chat`, `/v1/chat/with-file`, upload metadata and model usage/cost events.

Retrieve recent conversations:

```bash
curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/conversations?limit=20" \
  -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

Read one conversation with messages:

```bash
curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/conversations/CONVERSATION_ID?limit=100" \
  -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

Resume context in `/v1/chat` by sending the previous `conversation_id`. HIVE will hydrate recent user/assistant turns from SQL before adding the new message:

```bash
curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/chat" \
  -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{"conversation_id":"CONVERSATION_ID","message":"Continue from the previous answer in one sentence.","mode":"general","db_history_limit":20}"
```

Inspect persisted file metadata and model costs:

```bash
curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/files?limit=20" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"

curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/cost-summary" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

List D1 ecosystem metadata records:

```bash
curl -X GET "https://YOUR-KOYEB-APP.koyeb.app/v1/db/ecosystem-metadata?lane=rams&limit=20" \
  -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```


### Chunk retrieval foundation

After persistence is enabled and `/v1/db/init` has run, HIVE can index stored text-ish files into SQL chunks:

```bash
curl -X POST "$HIVE_URL/v1/files/chunk" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"object_key":"uploads/example/file.txt"}'
```

List chunks:

```bash
curl "$HIVE_URL/v1/files/chunks?key=uploads/example/file.txt&include_content=false" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Search chunks:

```bash
curl "$HIVE_URL/v1/files/chunks/search?key=uploads/example/file.txt&query=deployment%20failure&limit=6" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Ask using persisted chunks rather than the raw file excerpt:

```bash
curl -X POST "$HIVE_URL/v1/chat/with-file" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"object_key":"uploads/example/file.txt","message":"What does this file say about deployment failure?","use_chunks":true,"chunk_limit":6}'
```

Relevant env controls:

```env
FILE_CHUNK_MAX_CHARS=4000
FILE_CHUNK_OVERLAP_CHARS=400
FILE_CHUNK_MAX_COUNT=500
FILE_RETRIEVAL_MAX_CHUNKS=6
```


## v1.3 Vectorize foundation

Vectorize is optional and gated. PostgreSQL `hive_file_chunks` remains the source of truth; HIVE uses SQL chunk IDs as Vectorize vector IDs. If Vectorize or embeddings are disabled or fail, chunk-aware chat can fall back to SQL lexical retrieval.

Recommended flow:

```bash
curl "$HIVE_URL/v1/vectorize/diagnostics" -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"

curl -X POST "$HIVE_URL/v1/files/vectorize" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"object_key":"uploads/example/file.txt","auto_chunk":true}'

curl "$HIVE_URL/v1/files/vector-search?key=uploads/example/file.txt&query=deployment%20failure&limit=6" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN"
```

Chunk-aware chat can request semantic retrieval like this:

```json
{
  "object_key": "uploads/example/file.txt",
  "message": "What does this file say about deployment failure?",
  "use_chunks": true,
  "use_vectorize": true,
  "vectorize_fallback_sql": true,
  "chunk_limit": 6
}
```

Env controls:

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


## v1.4 operational polish

This build keeps PostgreSQL chunks as the source of truth and uses Vectorize as an optional semantic accelerator. File-chat and vector-search responses now expose stable retrieval metadata:

- `retrieval_source`
- `retrieval_mode`
- `vector_hits`
- `sql_fallback_hits`
- `fallback_used`

`/health` now reports structured storage flags for R2, SQL, D1, Vectorize and embeddings. `/healthz` is intentionally tiny so MAST can later call it to keep the Koyeb service awake without touching admin APIs.

Token hygiene: rotate Cloudflare/OpenRouter/admin tokens after any accidental paste into chat or logs, then update the matching Koyeb secret and redeploy.

## v1.5 ingestion expansion for Koyeb free tier

Build stage `v1.7-ecosystem-intelligence` adds bounded archive/document ingestion without turning HIVE into a heavy always-on worker. This matters because the current deployment is on a free Koyeb web service.

New/expanded capabilities:

- `/v1/files/zip/extract-text` extracts bounded text from ZIP archives, including nested ZIPs up to `ZIP_EXTRACT_MAX_DEPTH`.
- DOCX, XLSX, PDF, CSV, JSON and HTML extraction now reports structured metadata and truncation status.
- `/v1/files/chunk` and `chat/with-file` use document extractors for binary office/PDF files instead of treating them as raw UTF-8.
- Free-tier limits are visible from `/health` under `free_tier.ingestion_limits`.
- MAST can use `/healthz` as a tiny keep-awake target.

Recommended free-tier defaults:

```env
HIVE_FREE_TIER_MODE=true
DOCUMENT_EXTRACT_MAX_CHARS=120000
ZIP_EXTRACT_MAX_MEMBERS=80
ZIP_EXTRACT_MAX_MEMBER_BYTES=2097152
ZIP_EXTRACT_MAX_TOTAL_TEXT_CHARS=120000
ZIP_EXTRACT_MAX_DEPTH=2
```

The intended real workflow is now: upload an audit/report ZIP to R2, extract a bounded text artefact, chunk it into PostgreSQL, optionally Vectorize those chunks, and ask HIVE questions over the extracted package.

## v1.6 workflow presets and R2 lane registry

Build stage `v1.7-ecosystem-intelligence` turns HIVE from a generic file-aware chatbot into a small private ops analyst with labelled workflows.

Workflow presets currently available:

- `audit_report_review` for RAMS/AIMS audit bundles and QA findings.
- `repo_debug_bundle` for repo ZIPs, stack traces, CI artefacts and patch planning.
- `ci_log_analysis` for exact log/error triage using cheap SQL retrieval first.
- `social_content_qa` for brand/content quality review.
- `podcast_episode_review` for transcript, metadata and amplification checks.
- `ebook_keyword_review` for catalogue/keyword review without inventing marketplace data.

A file-chat request can now include:

```json
{
  "object_key": "uploads/example/audit-extracted.txt",
  "message": "Summarise the highest-risk findings.",
  "workflow_preset": "audit_report_review",
  "dry_run": true
}
```

Preset responses include:

- `workflow_preset` with the selected preset details.
- `retrieval_metadata` for Vectorize/SQL behaviour.
- `retrieval_summary` with confidence and fallback notes.
- `source_chunks[]` with compact chunk IDs, object keys, scores and excerpts for UI citations.

The R2 ecosystem lane registry reads the bucket/base-url envs for audits, blog, podcast, transcripts, RSS, brand assets and HIVE skills. In v1.6 this is safe registry/public-URL awareness first; primary upload/read still uses the main HIVE upload bucket. Multi-bucket read/write can be added later if the workflows prove useful.
Run the local v1.6 smoke helper after deployment:

```bash
ADMIN_BEARER_TOKEN=your-token python scripts/v16_smoke.py
```

To include a dry-run preset chat check:

```bash
ADMIN_BEARER_TOKEN=your-token HIVE_TEST_OBJECT_KEY=uploads/.../file.txt python scripts/v16_smoke.py
```


## v1.7 Ecosystem Intelligence

Build stage `v1.7-ecosystem-intelligence` adds lightweight cross-lane discovery without turning HIVE into a heavy background crawler. PostgreSQL chunks, Cloudflare Vectorize, D1 metadata, and the R2 lane registry remain separate, bounded layers.

New endpoints:

- `GET /v1/ecosystem/status` – MAST-friendly ecosystem status across SQL, D1, R2, Vectorize, embeddings, and the skills lane.
- `GET /v1/ecosystem/search?q=...` – searches D1 ecosystem metadata and enriches results with lane/public URL hints.
- `GET /v1/ecosystem/recent` – returns recent indexed ecosystem metadata, grouped by lane.
- `GET /v1/files/r2-discovery` – bounded R2 lane preview. This lists a small number of objects only and does not read object bodies.
- `GET /v1/skills/search?q=...` – searches indexed shared skills metadata.
- `GET /v1/skills/list` – lists recent indexed skills metadata.

Free-tier note: v1.7 deliberately avoids large bucket walks, background polling, and recursive object reads. Use D1 metadata as the discovery index and R2 discovery only for small previews or diagnostics.
