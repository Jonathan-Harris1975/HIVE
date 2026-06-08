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

Initial scaffold. It includes the core backend shape, API routes, OpenRouter client, SSE stream handling, file ingestion, ZIP safety checks, R2 adapter, Vectorize adapter skeleton, model routing, token/cost scaffolding, and starter tests.

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

## Useful endpoints

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/stream`
- `POST /v1/files/upload`

## Environment

See `.env.example`.

## Security stance

- OpenRouter key is never sent to the client.
- Admin bearer token required outside dev mode.
- ZIP extraction uses path traversal checks and size/file-count limits.
- Unsupported files are stored and recorded, not blindly pushed into model context.
- Uploaded content should be indexed and selected before model use to avoid token bonfires.
