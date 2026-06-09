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
