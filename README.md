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

V1 scaffold with working OpenRouter chat, model routing, R2/local upload storage, JSON text upload, file listing, file read-back, and single-file chat. Vectorize and durable metadata persistence remain later layers.

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
- `GET /v1/files/list`
- `GET /v1/files/read?key=uploads/...`
- `POST /v1/chat/with-file`


### JSON text upload smoke test

Use this when testing from ReqBin, Make.com, or a phone where multipart `curl -F` uploads are awkward:

```bash
curl -X POST "https://your-koyeb-service.example/v1/files/upload-text" \
  -H "Authorization: Bearer $ADMIN_BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"filename\":\"hive-r2-smoke.txt\",\"content\":\"HIVE R2 smoke test. Upload pipeline working.\"}"
```

Expected response includes `storage`, `object_key`, `public_url`, `chunk_count`, and `supported_for_text`.

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

The v1 file-chat route injects a bounded text excerpt into the prompt. Vectorize/chunk retrieval will replace this later for larger corpora.

## Environment

See `.env.example`.

## Security stance

- OpenRouter key is never sent to the client.
- Admin bearer token required outside dev mode.
- ZIP extraction uses path traversal checks and size/file-count limits.
- Unsupported files are stored and recorded, not blindly pushed into model context.
- Uploaded content should be indexed and selected before model use to avoid token bonfires.
