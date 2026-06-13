# Roadmap

## v0.1 Scaffold

- [x] FastAPI app structure
- [x] OpenRouter model list endpoint
- [x] OpenRouter streaming client
- [x] SSE normalisation
- [x] Bearer token auth
- [x] R2 storage adapter
- [x] Local blob fallback
- [x] ZIP safety inspection
- [x] Text extraction skeleton
- [x] Model router
- [x] Brand / General / Code / Audit modes
- [x] Starter tests

## v0.2 Persistence

- [ ] SQLAlchemy models for conversations, messages, files, chunks, costs
- [ ] SQLite local database
- [ ] PostgreSQL production path
- [x] File list/read API
- [x] Single-file chat API
- [x] Public URL helper API
- [x] Base64 upload API for phone/ReqBin/Make testing
- [x] Stored ZIP inspection API
- [x] Durable file metadata API
- [x] Conversation resume API
- [x] Conversation listing/read API
- [x] Cost summary API
- [x] D1 metadata listing API

## v1.0 Test-ready baseline

- [x] Koyeb deployment contract
- [x] OpenRouter model/key smoke tests
- [x] Free-first fallback policy
- [x] R2 diagnostics/list/read cycle
- [x] JSON/base64 upload path for phone/ReqBin/Make tests
- [x] Stored ZIP inspection
- [x] Single-file chat with separate source metadata
- [x] Empty reply and truncation diagnostics

## v0.3 Search

- [ ] Embedding worker
- [ ] Cloudflare Workers AI embeddings bridge
- [ ] Vectorize upsert/query integration
- [ ] Source-cited RAG answers

## v0.4 UI

- [ ] React/Vite chat UI
- [ ] Mode selector
- [ ] Model picker
- [ ] Upload panel
- [x] R2 file browser API
- [x] Source metadata outside model replies
- [x] Empty-reply retry/diagnostics
- [x] Completion truncation flags
- [ ] R2 file browser UI
- [ ] Cost display

## v0.5 Ops features

- [ ] AIMS/RAMS audit report reader
- [ ] Quarantine report review
- [ ] Koyeb/GitHub log analysis upload mode
- [ ] Social/post QA lanes


## File-chat timeout diagnostics

`/v1/chat/with-file` supports `dry_run:true` / `skip_model:true` to verify R2 read and prompt construction without a model call. Configure the model-call guard with:

```env
CHAT_WITH_FILE_MODEL_TIMEOUT_SECONDS=30
```

The endpoint returns `stage`, `timings`, and `error_code:"chat_with_file_timeout"` instead of a hanging request when model calls exceed the guard.


## v1.1 Persistence retrieval

- [x] Persist non-streaming chat usage/cost events to SQL when enabled.
- [x] Persist upload/file metadata to SQL when enabled.
- [x] List recent conversations.
- [x] Read one conversation and recent messages.
- [x] Hydrate `/v1/chat` from stored conversation history when `conversation_id` is supplied.
- [x] List SQL file metadata records.
- [x] Summarise total and by-model token/cost usage.
- [x] List Cloudflare D1 ecosystem metadata records by lane.

Next persistence step: add embeddings/Vectorize on top of the now-stable chunk records rather than sending large files directly to the model.


## Completed v1.1 persistence hardening

- Production-safe PostgreSQL transaction rollback behaviour.
- True SQL upserts for conversations and files.
- SQL/D1 write probes via `/v1/db/ping-write`.
- D1 retry/diagnostic controls.
- Persistence docs and Koyeb env guidance updated.


## v1.2 Chunk retrieval foundation

- [x] Deterministic text chunking with overlap and token estimates.
- [x] SQL `hive_file_chunks` table for durable chunk storage.
- [x] `/v1/files/chunk` to create/recreate chunks for one stored file.
- [x] `/v1/files/chunks` to list chunk records for a file.
- [x] `/v1/files/chunks/search` for SQL-backed lexical retrieval.
- [x] `/v1/chat/with-file` chunk mode via `use_chunks:true`.
- [x] Optional `auto_chunk:true` bridge for smoke tests/small files.
- [x] Cloudflare Vectorize diagnostics, embeddings/upsert/query, SQL fallback and chunk-aware chat integration.


## v1.3 Vectorize foundation

- [x] `/v1/vectorize/diagnostics` for safe config/probe checks.
- [x] `/v1/files/vectorize` to embed SQL chunks and upsert to Vectorize.
- [x] `/v1/files/vector-search` with SQL fallback.
- [x] `/v1/chat/with-file` semantic retrieval via `use_vectorize:true`.
- [x] Vectorize IDs are SQL chunk IDs, keeping PostgreSQL as source of truth.


## v1.4 Operational polish

- [x] Add `retrieval_source`, `vector_hits`, `sql_fallback_hits` and `fallback_used` metadata.
- [x] Add dry-run-first smoke-test cleanup endpoint.
- [x] Patch `/health` with exact build and structured storage flags.
- [x] Add `/healthz` for future MAST keep-awake checks.
- [x] Add Vectorize index stats to diagnostics.
- [x] Add larger ZIP/document ingestion tests.
- [x] Add token-rotation guidance to docs.

Next: add MAST-side scheduling, dashboards and optional cleanup automation once the HIVE API surface remains stable for a few days.

## v1.5 Ingestion expansion

Completed:

- Bounded document extraction for DOCX/XLSX/PDF/CSV/JSON/HTML.
- Bounded recursive ZIP text extraction via `/v1/files/zip/extract-text`.
- Derived text artefact storage for archive packages.
- Optional immediate SQL chunking for extracted ZIP text.
- Free-tier ingestion limits surfaced in `/health`.
- MAST keep-awake support through `/healthz`.

Next logical phase: real-world audit/report ZIP testing, then targeted UI/ops-console affordances for selecting extracted artefacts and asking questions over them.

## v1.6 Workflow presets and R2 lanes

Completed:

- Added workflow presets for audit review, repo/debug bundles, CI/log analysis, social content QA, podcast review and eBook keyword review.
- Added `GET /v1/workflow-presets`.
- Added `workflow_preset` support to `/v1/chat/with-file`.
- Added grounded `source_chunks[]` metadata for UI citations.
- Added `retrieval_summary` with confidence and fallback notes.
- Added safe R2 ecosystem lane registry for the user's AIMS/RAMS/website/podcast/skills buckets.
- Added `/v1/files/r2-lanes` and `/v1/files/r2-lanes/public-url`.

Next logical phase: a minimal UI cockpit that can upload/select files, choose a workflow preset, ask a question, and show retrieval metadata/source chunks without needing the full dashboard yet.

## v1.7 – Ecosystem Intelligence

Completed in this stage:

- MAST-friendly ecosystem status endpoint.
- Cross-lane ecosystem metadata search over D1.
- Recent ecosystem metadata grouping by lane.
- Shared skills metadata search/list endpoints.
- Bounded R2 lane discovery previews.
- R2 lane public URL awareness retained from v1.6.

The next likely phase is a minimal operator UI over these endpoints rather than more backend storage plumbing.

## v1.8 – Skill Registry Import

Status: implemented.

- Import R2 shared skill-pool search documents into D1.
- Catalogue skills by priority tier, HIVE lane, risk level, repo membership and tags.
- Support filtered list/search for HIVE, RAMS, AIMS and Website consumers.
- Keep R2 as source of truth and D1 as the lightweight searchable catalogue.
- Dry-run import remains the default for safe phone/ReqBin testing.

## v1.9 – Intelligent Skill Search

Status: implemented.

The v1.9 layer fixes the v1.8 limitation where phrase searches such as `RSS rewrite` could return zero results even when relevant skill metadata existed. Search now loads the bounded D1 skill catalogue, applies optional filters, and scores results using weighted fields:

- exact title
- title
- slug
- tags
- HIVE lane
- catalogue category
- repo membership
- indexable text

Returned records include `matched_terms`, `matched_fields` and `score_explanation` so the future UI and review queue can explain why a skill was found.

## v1.10-v1.16 Skill Intelligence and Review Integration

Status: implemented and consolidated.

- Skill recommendation endpoint.
- Review-gated skill routing endpoint.
- Shared ecosystem execution-plan endpoint.
- D1-backed execution review queue.
- Audit trail and evidence-pack endpoints.
- Plan-only safety model preserved: no repo mutation, no skill execution, no background jobs.

## v1.17 – Registry Integrity

Status: implemented.

- Add `/v1/skills/integrity` for an overall registry health report.
- Add `/v1/skills/duplicates` for duplicate skill IDs, slugs, object keys and search-document IDs.
- Add `/v1/skills/missing` for required metadata and taxonomy validation.
- Add `/v1/skills/orphans` for descriptor URL/object-key/lane/source-type mismatch checks.
- Add `/v1/skills/rebuild-index` as a dry-run-first D1 catalogue maintenance endpoint.

Next logical phase: use integrity status as a prerequisite gate for stronger recommendation/routing workflows and later operator UI controls.

## v1.18-v1.19 Workflow Graphs and Controlled Preview

Status: implemented.

The v1.18/v1.19 line adds graph-shaped planning and controlled execution preview semantics. This is the correct bridge before any real execution adapter work. HIVE can show the operator what would happen, what is blocked, what requires review, and why execution is not yet available.

Next logical direction: operator-dashboard API polish and eventual allow-listed dry-run adapters, still behind review gates.


## v1.20-v1.22 Complete

- v1.20: Execution preview persistence.
- v1.21: Approval gates and policy profiles.
- v1.22: Pretend-mode workflow simulation.

The next logical branch is an operator dashboard API or R2 export writing for evidence packs, but only after review-gated preview history is stable in production.
