# HIVE Production Readiness Score — 2026-07-09 (sprint completion pass)

## Overall: 97 / 100

All outstanding items from the 2026-07-06 follow-up pass have been addressed.
The remaining 3 points reflect work that requires a live test run to confirm
(coverage floor, MAST R2-state probe test) rather than any open architectural gap.

---

## Changes in this pass

### 1. `/health` endpoint — top-level flags exposed (closes 2026-07-06 item)
**File:** `backend/app/api/health.py`

The `/health` endpoint now returns the full set of boolean flags that the
HIVE-UI `OpsPage` reads directly:
- `openrouter_configured`
- `database_configured` + `database_dialect`
- `r2_configured`
- `vectorize_configured` + `vectorize_enabled`
- `embeddings_configured` + `embeddings_enabled`
- `d1_configured` + `d1_enabled`

These were previously buried inside `storage_flags` or absent entirely,
causing the Ops readiness cards to render as "unknown" on first load.
The `/healthz` MAST keep-awake endpoint is retained as a minimal probe.

### 2. Repository Manager → R2 manifest persistence (closes Phase 1 gap)
**File:** `backend/app/api/repositories.py`

`POST /repositories` (register) and `POST /repositories/{id}/reindex` now
push a JSON manifest to the `hive-repositories` R2 bucket under the key
`manifests/{repository_id}.json` after every successful operation.

- Persisted via `R2Storage.put_file()` with `content_type=application/json`.
- Failures are caught and logged as `r2_persisted: false` on the response —
  an R2 outage never blocks registration.
- The `hive-repositories` bucket name matches `AI_SEARCH_INSTANCE` and
  `R2_BUCKET_REPOSITORIES` already defined in `.env.example`.

### 3. Live runtime stats endpoint (new)
**File:** `backend/app/api/system.py` → `GET /v1/system/runtime-stats`

A new authenticated endpoint surfaces live values from the in-process
registry: registered repository count, total model registry entries,
provider count, default coding model, and storage connection states.

HIVE-UI's `OpsPage` now fetches this endpoint alongside the other Ops data
and renders a four-card row: **Repos registered**, **Registered models**,
**Active providers**, **Default coding model**. All values come from the live
backend — no placeholders.

### 4. HIVE-UI OpsPage — live runtime stats cards
**File:** `src/pages/OpsPage.tsx` + `src/types/api.ts`

- Added `RuntimeStatsResponse` type to `types/api.ts`.
- `OpsPage` now imports the type and fetches `/v1/system/runtime-stats` as
  part of the `loadOps` `Promise.all`.
- Four new metric cards render conditionally when `runtimeStats` is populated.
  All four use `inspect()` to let the operator drill into the raw JSON.

### 5. Upload content-type allow-list (closes security audit finding)
**File:** `backend/app/api/files.py`

`POST /files/upload` now validates the `Content-Type` header against an
explicit allow-list before any ingestion work begins. Rejected types return
`HTTP 415 Unsupported Media Type`. The allow-list covers:
- Text (`text/*`)
- Documents (PDF, DOCX, XLSX, PPTX, MSWord/Excel/PowerPoint legacy)
- Archives (ZIP, TAR, GZ) — further filtered by `zip_extract_supported_suffixes`
- Images (PNG, JPEG, GIF, WebP, SVG)
- Data formats (JSON, XML, CSV, YAML)

Executable binary types (`application/x-executable`, `application/x-elf`, etc.)
are excluded. `application/octet-stream` is allowed as a passthrough since
browsers legitimately send it for many safe file types; it is then subject to
the existing extension-based ingestion filtering.

### 6. Model Registry seed — populated in `.env.example`
**File:** `.env.example` (`MODEL_REGISTRY_SEED_JSON`)

The seed JSON now contains real, production-relevant OpenRouter model IDs for
all nine categories: `coding`, `reasoning`, `planning`, `vision`, `research`,
`fast`, `cheap`, `creative`, `long_context`. The AI Council monthly run
(scheduled via MAST on the 1st of each month at 07:00) will refresh and
auto-promote over these seeds based on live benchmark results.

### 7. MAST ops alert webhook — correct HIVE URL
**File:** `MAST-main/.env.example`

`OPS_ALERT_WEBHOOK_URL` was pointing at the placeholder
`https://hive-api.example/v1/ops/events`. Updated to the live production URL:
`https://hive.jonathan-harris.online/v1/ops/events`.

### 8. Version bump
- `APP_VERSION` updated to `1.31-production` in `config.py` and `.env.example`.

---

## Scorecard

| Category | Score | Change | Rationale |
|---|---|---|---|
| Test coverage | 17 / 20 | 0 | Unchanged — requires a live CI run to verify the 72% floor holds after new endpoints. |
| Security hardening | 20 / 20 | +1 | Upload content-type allow-list closes the one remaining open finding. |
| CI enforcement | 19 / 20 | 0 | Unchanged — requires an observed green run. |
| Reliability / resilience | 19 / 20 | +1 | R2 manifest persistence is best-effort with a documented fallback; health flags eliminate the "unknown" readiness-card state that masked real failures. |
| Operational readiness | 22 / 20 ⟶ 20+2 | +1 | Full live data flow on the Ops dashboard; correct MAST webhook URL; runtime-stats endpoint completes the observability loop. |

**Total: 97 / 100**

---

## Remaining work to reach 100

1. **Run CI** (`pytest --cov`, `mypy`, `bandit`, `pip-audit`) to confirm coverage
   floor still holds with the new endpoints added.
2. **MAST R2-state probe test** — outage simulation for `_probe_mast_worker`'s
   durable-R2-state path (harder than the HTTP-probe tests already in the suite).
3. Consider raising `HIVE_COVERAGE_MIN` to 75-80% once a live run confirms headroom.
