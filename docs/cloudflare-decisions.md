# Cloudflare Decisions

## R2

Use Cloudflare R2 for object storage. The backend uses the S3-compatible API through `boto3`, which keeps the Python service portable.

## Redis / queue / cache decision

Cloudflare does not provide Redis as a Redis-compatible managed service.

Recommended approach:

1. Start without Redis for v1: use in-process model cache plus database-backed job state.
2. For Cloudflare-native edge coordination later, use Durable Objects, KV, Queues, or Workflows depending on the job.
3. If a real Redis protocol service becomes necessary, use a free external Redis provider such as Upstash free tier.

For this repo, Redis is not a hard v1 dependency.

## PostgreSQL decision

Cloudflare Hyperdrive connects to an existing PostgreSQL or MySQL database. It is a connection accelerator/proxy, not a hosted PostgreSQL database.

Options:

- Local/dev: SQLite.
- Low-cost production: Neon or Supabase PostgreSQL free tier via `DATABASE_URL`.
- Cloudflare-native SQL: D1, but this is SQLite-compatible rather than PostgreSQL.
- If deployed inside Workers later: Hyperdrive can connect Workers to an external Postgres database.

Current repo default: SQLite-compatible local database URL, with PostgreSQL-ready configuration.

## Vector search decision

Use Cloudflare Vectorize as the preferred vector store abstraction. Keep the adapter thin so pgvector can be used later if Vectorize becomes awkward for local development or metadata-heavy search.

## Embeddings

Preferred Cloudflare-native route:

- Workers AI embeddings
- Vectorize for indexing/querying

Fallback:

- Use OpenRouter-compatible embedding model if needed.
- Store vectors in pgvector if a single Postgres service becomes preferable.

## Cloudflare D1 metadata lane

D1 is optional in v1.1 and should be used for ecosystem metadata rather than full chat history. Recommended split:

- SQL/Koyeb PostgreSQL: conversations, messages, file metadata, upload records, token usage and cost tracking.
- Cloudflare D1: audit run index, council report index, podcast episode index, ebook catalogue cache, social performance snapshots and other AIMS/RAMS ecosystem metadata.

Required envs:

```env
D1_ENABLED=true
D1_ACCOUNT_ID=your-cloudflare-account-id
D1_API_KEY=your-d1-api-token
D1_DATABASE_ID=your-d1-database-uuid
D1_DATABASE_NAME=database-hive
```

Initialise the D1 schema with:

```bash
curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/init" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN"
```

Add a metadata smoke record:

```bash
curl -X POST "https://YOUR-KOYEB-APP.koyeb.app/v1/db/ecosystem-metadata" -H "Authorization: Bearer YOUR_ADMIN_BEARER_TOKEN" -H "Content-Type: application/json" -d "{\"lane\":\"rams\",\"source_type\":\"audit_run\",\"title\":\"D1 smoke test\",\"metadata\":{\"ok\":true}}"
```


## v1.4 Cloudflare operational notes

Vectorize is used through the REST API from Koyeb. A Worker binding is not required for HIVE because the FastAPI service calls Vectorize directly.

The `hive-chunks` index should match the embedding dimensions used by Workers AI. For `@cf/baai/bge-base-en-v1.5`, use 768 dimensions and cosine distance.

Recommended split remains:

- R2: raw file bodies and uploaded ZIP/document objects.
- D1: ecosystem metadata indexes.
- Vectorize: semantic lookup only, with SQL chunk IDs as vector IDs.
- PostgreSQL: durable source of truth for conversations, files, chunks and cost events.

Rotate Cloudflare API tokens after accidental exposure. Update the Koyeb secret, redeploy, and verify `/v1/vectorize/diagnostics`.

## v1.6 R2 ecosystem lanes

The following public, non-secret R2 lane envs are recognised by HIVE:

- `R2_BUCKET_AUDITS` / `R2_PUBLIC_BASE_URL_AUDITS`
- `R2_BUCKET_BLOG` / `R2_PUBLIC_BASE_URL_BLOG`
- `R2_BUCKET_BLOG_IMAGES` / `R2_PUBLIC_BASE_URL_BLOG_IMAGES`
- `R2_BUCKET_BLOG_RSS` / `R2_PUBLIC_BASE_URL_BLOG_RSS`
- `R2_BUCKET_BRAND_ASSETS` / `R2_PUBLIC_BASE_URL_BRAND_ASSETS`
- `R2_BUCKET_META` / `R2_PUBLIC_BASE_URL_META`
- `R2_BUCKET_META_SYSTEM` / `R2_PUBLIC_BASE_URL_META_SYSTEM`
- `R2_BUCKET_PODCAST` / `R2_PUBLIC_BASE_URL_PODCAST`
- `R2_BUCKET_PODCAST_RSS_FEEDS` / `R2_PUBLIC_BASE_URL_PODCAST_RSS`
- `R2_BUCKET_RSS_FEEDS` / `R2_PUBLIC_BASE_URL_RSS`
- `R2_BUCKET_TRANSCRIPTS` / `R2_PUBLIC_BASE_URL_TRANSCRIPT`
- `R2_BUCKET_HIVE_SKILLS` / `R2_PUBLIC_BASE_URL_HIVE_SKILLS`

Use `GET /v1/files/r2-lanes` to inspect the configured registry. Use `GET /v1/files/r2-lanes/public-url?lane=audits&key=...` to safely build a public URL. This build does not yet add multi-bucket write automation; that is deliberate to avoid accidental cross-bucket writes while HIVE is still on the free web service.
