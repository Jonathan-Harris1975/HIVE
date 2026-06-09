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
- Cloudflare Vectorize-ready abstraction for semantic search.
- Pluggable metadata store: SQLite for local dev, PostgreSQL/D1 adapter later.

## Current status

V1 test-ready build with working OpenRouter chat, model routing, empty-reply hardening, R2/local upload storage, JSON/base64 uploads, file listing, file read-back, public URL helper, stored ZIP inspection, and single-file chat. Vectorize and durable metadata persistence remain later layers.

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
- `GET /v1/models`
- `POST /v1/chat/stream`
- `POST /v1/files/upload`
- `POST /v1/files/upload-text`
- `POST /v1/files/upload-base64`
- `GET /v1/files/list`
- `GET /v1/files/read?key=uploads/...`
- `GET /v1/files/public-url?key=uploads/...`
- `GET /v1/files/zip/inspect?key=uploads/...`
- `POST /v1/chat/with-file`
  - Supports `dry_run:true` / `skip_model:true` for file-read and prompt-build diagnostics.
- `GET /v1/db/diagnostics`
- `POST /v1/db/init`
- `GET /v1/db/conversations`
- `GET /v1/db/conversations/{conversation_id}`
- `GET /v1/db/files`
- `GET /v1/db/cost-summary`
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
