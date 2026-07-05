> **Document status:** Production reference  
> **Last reviewed:** 5 July 2026  
> **Operational authority:** Current repository README, SECURITY policy and operations guide.

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

PostgreSQL remains the source of truth for file metadata and chunks. Vectorize remains an optional semantic accelerator over SQL chunk IDs. Production resource protection is provided by explicit member, byte, character and recursion limits.

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

The R2 ecosystem lane registry is metadata-first. It records configured bucket names and public base URLs for uploads, audits, blog artefacts, images, RSS feeds, brand assets, podcast artefacts, transcripts and HIVE skills. The primary upload lane remains the only direct read/write storage adapter in this build. This keeps paid-production resource use predictable while letting HIVE understand where wider AIMS/RAMS/website/podcast artefacts live.


## v1.12 shared ecosystem execution layer

HIVE now contains a review-gated skill intelligence stack:

```text
D1 skill catalogue
  -> weighted skill search
  -> recommendation engine
  -> review-gated routing
  -> shared ecosystem execution plan
```

The execution layer does not mutate repos, install skills, run deploys or start background workers. It returns reviewable plans for HIVE/AIMS/RAMS/Website workflows and keeps PostgreSQL, D1, R2, Vectorize and the skill registry as separate, bounded layers.

## v1.15 execution review queue

The execution review queue sits between skill routing and the production adapter handoff. It stores reviewable plan records in D1 using lane `hive_execution_reviews`. Each record contains the routed skill plan, task, repo, workflow preset, review gate state, decision log and adapter gate state.

Pending reviews remain non-executable. An `approved` review now records `can_execute_now:true`, `adapter_execution_enabled:true` and `execution_state:ready_for_execution`, so HIVE-UI no longer treats production approval as review-only. The approval decision unlocks the allow-listed handoff; it does not auto-run repo pushes, package installs or background jobs.

## v1.15 review evidence packs

The evidence-pack layer sits on top of the execution review queue. It turns a stored D1 review record into a UI/export friendly artefact with task metadata, primary skill evidence, candidate skills, shared execution steps, guardrails, decision log and audit timeline.

Evidence packs remain inline review/export responses. Approved packs can signal readiness for allow-listed production handoff, but the export response itself does not push repos, install packages or start background work.

## v1.17 Registry Integrity Layer

The shared skills catalogue is now treated as a governed registry rather than a loose list of descriptors. HIVE keeps R2 as the descriptor source of truth and D1 as the searchable catalogue, then validates the D1 records through read-only integrity endpoints.

The registry integrity layer checks:

- duplicate skill IDs, slugs, object keys and search-document IDs;
- required metadata fields;
- priority tier, risk level and repo taxonomy;
- descriptor URL/object-key consistency;
- D1 lane and source-type consistency.

`/v1/skills/rebuild-index` is deliberately dry-run-first and only upserts D1 metadata from the shared R2 search documents. It does not mutate R2, install packages, execute skills or write to repos.

## v1.18/v1.19 Workflow Graph and Controlled Preview Layer

The workflow graph layer converts task, repo, workflow preset and skill-routing context into a UI-friendly graph:

```text
request -> classify -> recommend_skills -> collect_evidence -> dry_run_output -> risk_gate -> review_queue -> adapter_execution(ready_after_approval)
```

The controlled execution preview layer then annotates graph nodes with statuses, blockers and next actions. Pending plans wait at the review gate; approved plans mark the production adapter handoff as `ready_for_execution` and set `can_execute_now:true`.


## v1.20-v1.22 Preview Persistence, Policy Profiles and Simulation

The preview-persistence layer sits above workflow graphs and controlled execution preview. It stores preview records in D1, exposes reusable policy profiles, and simulates what an approved production adapter handoff will touch before the operator triggers it.

Data remains split deliberately:

- D1 stores lightweight preview metadata and simulation summaries.
- PostgreSQL remains the conversation/file/chunk persistence layer.
- R2 remains artefact storage.
- Vectorize remains semantic retrieval.

The simulation endpoint is deterministic and production-bounded. It estimates service touches, risk class, affected repos/buckets and missing prerequisites without model calls or external mutations.


## v1.27-v1.32 Repository Intelligence Platform (Phases 1-14)

A fourteen-phase programme layered on top of everything above, scoped strictly
to the HIVE backend repository (never HIVE-UI, AIMS, or RAMS). Every phase
reuses an existing pattern rather than introducing a parallel one:
`hive_ecosystem_metadata` (D1) backs every new history/registry table need
instead of new schemas; the workflow/skill/model-router patterns are extended
rather than replaced.

**Phase 1 - Repository Manager** (`services/repository_manager.py`): safe ZIP
extraction into a per-process temp working directory (reusing the existing
`zip_ingestion` path-traversal guards), fingerprinting, manifest generation
(language + dependency detection), incremental re-indexing, an in-process
registry, and TTL-based cleanup. Extraction is never permanent.

**Phase 2 - Repository Memory** (`services/repository_memory.py`,
`storage/ai_search.py`): Project DNA, architecture summary, coding standards,
build/deployment profiles, environment schema, and append-only history
(known issues, learned patterns, previous patches, optimisation/QA/Council
history) — all stored as rows in the existing `hive_ecosystem_metadata` D1
table under `lane="repository_memory"`. Cloudflare AI Search (instance
`hive-repositories`) provides semantic query without reloading a repository.

**Phase 3 - Model Registry** (`services/model_registry.py`): ranked models
per category (coding, reasoning, planning, vision, research, fast, cheap,
creative, long_context). `ModelRouter.select_model` prefers the top-ranked
"coding" model once the registry is populated, falling back to the static
`code_model` setting otherwise — additive, not a breaking change.

**Phase 4 - Provider Framework** (`services/providers/`): a uniform adapter
shape (models, pricing, context, tool/structured-output support, health,
latency). `OpenRouterProvider` wraps the existing `OpenRouterClient`;
`OpenRouterCompatibleProvider` is a generic adapter for any future
OpenRouter-shaped provider, added purely through
`PROVIDER_FRAMEWORK_EXTRA_PROVIDERS_JSON` configuration.

**Phase 5 - AI Council** (`services/ai_council.py`): discovers providers,
refreshes catalogues, diffs new/retired models against the last snapshot,
scores coding-capable models with the Benchmark Engine, auto-promotes above
`AI_COUNCIL_PROMOTION_THRESHOLD` into the Model Registry, and notifies
downstream services via the existing ops-event inbox. **Scoring caveat**: no
live coding/reasoning benchmark data source is wired in; metrics are derived
only from price/context/declared-capability signals until a real benchmark
integration exists.

**Phase 6 - Benchmark Engine** (`services/benchmark_engine.py`): configurable
weighted scoring across ten metric axes (coding/reasoning benchmarks, cost,
latency, reliability, long-context, JSON reliability, structured output,
community maturity, internal historical performance). Missing axes default
neutral rather than zero.

**Phase 7 - Repository QA** (`services/repository_qa.py`): a *static-only*
validation pipeline (build/lint/type/dependency/import/dead-code/security/
regression/patch/architecture checks) over a Phase 1 working copy. It
deliberately never installs dependencies or executes the repository's own
build/test commands — uploaded ZIPs are untrusted input, and doing so would
be an arbitrary-code-execution risk inside HIVE's own process. Dead-code
detection reuses the existing `repo_hygiene_report`.

**Phase 8 - Repository Council** (`services/repository_council.py`): scores
nine review dimensions (architecture, documentation, dependencies, technical
debt, security, performance, maintainability, AI-generated code, repository
health), most derived from the Phase 7 QA report, with configurable
dimension weights. "Performance" and "AI-generated code" are explicitly
documented low-confidence placeholders pending real profiling/classifier
data. History persists via Repository Memory's `repository_council_history`.

**Phase 9 - Bucket Manager** (`services/bucket_manager.py`): the explicit
accessible/hidden bucket registry from the programme spec, with an
`assert_accessible()` guard so hidden buckets (`metasystem`,
`podcast-chunks`, etc.) can never surface through normal workflows.

**Phase 10 - Connector Framework** (`services/connectors/`): a lighter-weight
diagnostic wrapper (health, authentication, capabilities, rate limits) around
the four initial connectors — OpenRouter (wraps the Phase 4 provider), R2
(wraps `R2Storage`), Cloudflare AI Search (wraps the Phase 2 adapter), and
GitHub (new, minimal REST client for repo metadata + rate limit).

**Phase 11 - Optimisation Engine** (`services/optimisation_engine.py`):
records every optimisation decision with `previous_state`/`new_state` and a
confidence score, supports rollback, and tracks experiment success rates.
The engine is the ledger of what to revert to; actually re-applying a
reverted state to whatever system it touched is the caller's responsibility.

**Phase 12 - Repository Learning** (`services/repository_learning.py`):
records patch outcomes, coding patterns, and repository-scoped model
preferences into the existing Repository Memory history fields, then rolls
that history up into a refreshed `project_dna` summary on demand.

**Phase 13 - Environment** (`services/env_audit.py`): audits every `Settings`
field's declared environment variable name(s) against `.env.example` so
infrastructure-configuration drift is caught by a tool rather than by
inspection. Running this audit while building Phase 10 caught a real gap
(`GITHUB_TOKEN`/`GITHUB_REPOSITORY` had been added to `Settings` but not yet
to `.env.example`) before it shipped.

**Phase 14 - Documentation**: this section, the per-phase `docs/releases/`
notes, and the updated `.env.example`/README endpoint table.

### Extension points

- New provider: add an entry to `PROVIDER_FRAMEWORK_EXTRA_PROVIDERS_JSON` —
  no new adapter code required if it exposes an OpenRouter-shaped `/models`.
- New connector: add a `services/connectors/<name>_connector.py` exposing
  `async def report(settings) -> ConnectorReport` and register it in
  `services/connectors/registry.py`.
- New Repository Memory field: extend `SCALAR_FIELDS`/`HISTORY_FIELDS` in
  `services/repository_memory.py` — no schema change needed, it's still the
  same `hive_ecosystem_metadata` table.
- New Model Registry category: extend `CATEGORIES` in
  `services/model_registry.py`; wire a router preference into
  `ModelRouter.select_model` the same way `TaskType.CODE` was wired.
- New Repository QA / Council check: add a `_check_*` function and include
  it in the `checks` list — each check is independent and additive.

