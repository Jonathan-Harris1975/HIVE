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
