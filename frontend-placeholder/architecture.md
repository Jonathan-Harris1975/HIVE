# Architecture

## Principle

Standalone first. External repos are studied, not inherited.

## Core flow

```text
User request
  -> Auth middleware
  -> Mode classifier: Auto / Brand / General / Code / File / Audit
  -> Task classifier
  -> Model router
  -> Context manager
  -> OpenRouter streaming client
  -> SSE response
```

## File flow

```text
Upload
  -> size check
  -> safe temp file
  -> sha256
  -> R2 or local fallback storage
  -> type detection
  -> ZIP inspection if needed
  -> text extraction if supported
  -> chunking
  -> metadata/indexing later
```

## Modes

- Auto: infer suitable mode.
- Brand: Jonathan Harris ecosystem tone and context.
- General: neutral assistant mode.
- Code: strict technical/code review mode.
- File Analysis: source-grounded file mode.
- Audit: production-readiness and QA mode for AIMS/RAMS/workflows.

## Model routing

Initial tiers:

- Cheap: summaries and quick triage.
- Balanced: normal file analysis and general work.
- Premium: critical reasoning.
- Code: repo/code/debugging.
- Audit: production, quarantine, CI, RAMS/AIMS analysis.

## SSE streaming

The backend uses a single OpenRouter streaming wrapper and normalises events to:

- `token`
- `keepalive`
- `error`
- `done`

## Source strategy

The app should answer from extracted/indexed chunks, not whole raw files, to reduce cost and improve traceability.


## Project glossary

- HIVE = the private OpenRouter-powered ops chatbot/console in this repo.
- AIMS = the user's AI/content automation and management ecosystem.
- RAMS = the user's reporting, audit, monitoring, and production-readiness system for AI/content workflows.
- Do not use the construction/legal “Risk Assessment Method Statement” meaning of RAMS unless explicitly requested.


## v1 reliability guardrails

- Explicit model selection is honoured, then preflighted where possible.
- Free-first fallback is used unless `ALLOW_PAID_FALLBACK=true`.
- Empty visible model replies are retried and surfaced as structured diagnostics.
- File-chat answers keep source metadata outside the model reply.
- R2 operations return JSON diagnostics instead of opaque Bad Gateway errors.

## Optional v1.1 persistence layer

HIVE now has an optional two-store persistence design:

| Store | Recommended use |
| --- | --- |
| Koyeb/PostgreSQL or local SQLite | Conversations, messages, upload records, file metadata, cost tracking and token usage logs. |
| Cloudflare D1 | Ecosystem metadata indexes such as audit runs, council reports, podcast episodes, ebook catalogue cache and social performance snapshots. |

The core v1 chat/R2 file loop still works with both stores disabled. Persistence is additive, not a hard dependency.

### SQL schema

`POST /v1/db/init` creates these SQL tables when `DATABASE_ENABLED=true`:

- `hive_conversations`
- `hive_messages`
- `hive_files`
- `hive_cost_events`

The same schema works for local SQLite smoke tests and Koyeb/PostgreSQL.



### Persistence production rules

- PostgreSQL writes use transaction context managers with rollback-on-error.
- Conversation and file records use `ON CONFLICT` upserts, avoiding PostgreSQL aborted-transaction poisoning.
- Each diagnostic table count runs safely so one missing table cannot poison the remaining checks.
- D1 queries include bounded retries and structured diagnostics.
- `/v1/db/ping-write` verifies SQL and D1 write/delete paths without leaving permanent records.

### D1 schema

`POST /v1/db/init` also creates `hive_ecosystem_metadata` when `D1_ENABLED=true`. This table is intentionally generic so RAMS/AIMS indexes can be added without a schema rewrite every time a new audit lane appears.


## File-chat timeout diagnostics

`/v1/chat/with-file` supports `dry_run:true` / `skip_model:true` to verify R2 read and prompt construction without a model call. Configure the model-call guard with:

```env
CHAT_WITH_FILE_MODEL_TIMEOUT_SECONDS=30
```

The endpoint returns `stage`, `timings`, and `error_code:"chat_with_file_timeout"` instead of a hanging request when model calls exceed the guard.


### Persistence retrieval flow

When SQL persistence is enabled, non-streaming chat endpoints write:

```text
/v1/chat or /v1/chat/with-file
  -> hive_conversations
  -> hive_messages
  -> hive_cost_events
```

Upload endpoints write file metadata to `hive_files`. Read/list endpoints continue to use R2/local object storage as the source of truth for bytes.

Conversation resume is intentionally conservative:

```text
incoming /v1/chat with conversation_id
  -> load recent SQL user/assistant turns
  -> add explicit request.history if supplied
  -> add current user message
  -> build OpenRouter payload
```

This keeps current prompts deterministic while allowing HIVE to continue an existing session without relying on client-side history alone.

D1 remains separate and stores ecosystem metadata indexes rather than full chat history.


## Chunk retrieval foundation

HIVE stores raw file bodies in R2/local blob storage and stores retrieval metadata in SQL. The `hive_file_chunks` table is the stable bridge between file storage and future vector search.

Current v1.2 flow:

1. Upload file to R2/local storage.
2. Run `POST /v1/files/chunk` for supported text-ish files.
3. Store deterministic overlapping chunks in SQL.
4. Use `GET /v1/files/chunks/search` for lightweight lexical retrieval.
5. Use `/v1/chat/with-file` with `use_chunks:true` to answer from selected chunks instead of injecting the full file excerpt.

Vectorize remains optional and disabled until the chunk table has proved stable in production. When enabled later, Vectorize should index these chunk IDs rather than inventing a parallel storage contract.


## v1.4 operational view

HIVE now has a durable retrieval spine:

```text
R2 raw files
  -> PostgreSQL file metadata
  -> PostgreSQL chunks as source of truth
  -> Workers AI embeddings
  -> Vectorize semantic lookup keyed by SQL chunk IDs
  -> SQL lexical fallback if Vectorize is unavailable
```

File-chat responses surface `retrieval_source`, `vector_hits`, `sql_fallback_hits` and `fallback_used` so operators can see whether an answer came from Vectorize or SQL fallback.

`/health` reports clean storage flags. `/healthz` is a minimal unauthenticated keep-awake point for future MAST monitoring.

## v1.5 ingestion architecture

HIVE now treats archive ingestion as a bounded transformation, not a background-heavy extraction worker. Raw uploaded ZIPs remain in R2. `/v1/files/zip/extract-text` reads the ZIP, extracts text from supported members into a single derived text artefact, stores that artefact back through the normal upload path, and can immediately create SQL chunks for retrieval.

PostgreSQL remains the source of truth for file metadata and chunks. Vectorize remains an optional semantic accelerator over SQL chunk IDs. Koyeb free-tier protection is provided by explicit member, byte, character and recursion limits.

## v1.6 workflow and lane architecture

HIVE v1.6 adds a lightweight workflow layer above file-chat. The API remains simple, but `workflow_preset` now controls the safest default mode, retrieval behaviour and output framing for common operational tasks.

```text
Stored file / extracted artefact
  -> optional workflow preset
  -> preset-tuned chunk retrieval
  -> Vectorize semantic lookup when enabled
  -> SQL fallback/source of truth
  -> answer + retrieval_summary + source_chunks[]
```

The current presets are:

- `audit_report_review`
- `repo_debug_bundle`
- `ci_log_analysis`
- `social_content_qa`
- `podcast_episode_review`
- `ebook_keyword_review`

The R2 ecosystem lane registry is metadata-first. It records configured bucket names and public base URLs for uploads, audits, blog artefacts, images, RSS feeds, brand assets, podcast artefacts, transcripts and HIVE skills. The primary upload lane remains the only direct read/write storage adapter in this build. This keeps Koyeb free-tier behaviour predictable while letting HIVE understand where wider AIMS/RAMS/website/podcast artefacts live.


## v1.12 shared ecosystem execution layer

HIVE now contains a plan-only skill intelligence stack:

```text
D1 skill catalogue
  -> weighted skill search
  -> recommendation engine
  -> review-gated routing
  -> shared ecosystem execution plan
```

The execution layer does not mutate repos, install skills, run deploys or start background workers. It returns reviewable plans for HIVE/AIMS/RAMS/Website workflows and keeps PostgreSQL, D1, R2, Vectorize and the skill registry as separate, bounded layers.

## v1.14 execution review queue

The execution review queue sits between skill routing and any future execution adapter. It stores reviewable plan records in D1 using lane `hive_execution_reviews`. Each record contains the routed skill plan, task, repo, workflow preset, review gate state and decision log.

The queue is intentionally non-executing. Even an `approved` review keeps `can_execute_now:false`. Later builds may add explicit adapters for selected low-risk operations, but those adapters should read this queue rather than bypass it.
