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

### D1 schema

`POST /v1/db/init` also creates `hive_ecosystem_metadata` when `D1_ENABLED=true`. This table is intentionally generic so RAMS/AIMS indexes can be added without a schema rewrite every time a new audit lane appears.
