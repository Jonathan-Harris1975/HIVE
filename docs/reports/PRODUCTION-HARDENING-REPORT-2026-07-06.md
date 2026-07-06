# HIVE Production Hardening Report

**Date:** 2026-07-06
**Scope:** Repository hygiene, security audit, resilience review, ecosystem validation, operational readiness, repository cleanup.
**Constraint:** This pass was performed by static code review only. The sandbox this audit ran in has no outbound network access, so dependencies could not be installed and `pytest`/`mypy`/`bandit`/`pip-audit` could not be executed live. All findings below are based on reading the actual source, config, and CI definitions in this repository — not on assumptions. Anything that genuinely requires a live run is called out explicitly in the risk register rather than claimed as verified.

No new features were added and no architecture was changed. Work was limited to hygiene, verification, and file reorganization.

---

## 1. Repository hygiene

- Removed 6 committed `__pycache__` directories, 1 `.pytest_cache` directory, and 31 stray `.pyc` files.
- `.gitignore` already correctly lists `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/` — no change needed. The presence of the artifacts above means they were committed *before* these rules were added, not that the rules are wrong. Re-verify with `git status --ignored` after committing this cleanup to confirm nothing reappears.

## 2. Security hardening — audit findings

**Route-level authorization:** every router except `health.py`, `runtime.py` (public probes), and `ops_events.py` / `optimisation_engine.qa_ingest_router` (which use their own bearer-token dependencies) applies `dependencies=[Depends(require_admin)]` at the router level, so no route in those routers can accidentally ship unauthenticated. `require_admin` uses `secrets.compare_digest` for constant-time comparison and is backed by a real IP+token-scoped auth-failure rate limiter (`core/rate_limit.py`, 10 failures/60s → 5 min lockout).

**Ops/RAMS ingestion endpoints** (`/ops/events`, RAMS QA ingest) are correctly gated behind their own dedicated bearer tokens, separate from the admin token and from each other — good separation of trust boundaries.

**Request size limits:** enforced twice — once from `Content-Length` before the body is read, and again as bytes stream in (`ProductionMiddleware`), so a client can't lie about `Content-Length` to bypass the limit. Default cap is 110 MB, applied uniformly.

**CORS / trusted hosts:** both are wired in `main.py`. More importantly, `core/production.py` fails startup in production if: CORS origins are wildcard/localhost/non-HTTPS, `ADMIN_BEARER_TOKEN` is the dev default or under 32 chars, `ALLOWED_HOSTS` is still `*`, or any enabled ingest token is under 32 chars. This is fail-closed behavior, not just documentation — confirmed by reading `enforce_production_readiness`, which is called from the app's `lifespan` and raises before the app starts serving.

**Upload / zip handling:** zip extraction is bounded by member count, per-member byte size, total extracted text, and recursion depth (`ingestion/zip_ingestion.py`) — real zip-bomb protection, not just a size check on the outer file. Downloaded/returned filenames are stripped of `\r`/`\n` before being placed in `Content-Disposition` headers, which prevents header-injection via a crafted object key.

**Findings to address:**

1. **`/health` discloses more than it should for an unauthenticated endpoint.** It returns which integrations are configured/enabled (R2, Vectorize, embeddings provider/model, database dialect, OpenRouter configured) with no auth. `/readyz` — the endpoint explicitly designed to avoid this — states in its own docstring that it returns "readiness without secret or integration details," but `/health` doesn't follow that same discipline. No secrets are exposed, but it's free reconnaissance about your stack for anyone who finds the URL. **Recommendation:** trim `/health` to what `/healthz` and `/readyz` already expose, and move the detailed flags behind `/v1/runtime/readiness` (already authenticated).
2. **Dead code path in `require_admin`:** the `if request.url.path == "/health": return` bypass at the top of `require_admin` is unreachable in practice since `health.py`'s router never applies `require_admin` as a dependency. Harmless today, but worth deleting so a future refactor can't accidentally rely on it as a real bypass mechanism.
3. **Direct file-upload endpoints accept an unvalidated `content_type` string from the caller** rather than checking it against an allow-list or sniffing actual content. Risk is reduced because the whole `files` router requires the admin bearer token, but it's still worth a basic allow-list if these uploads are ever rendered or served back with attacker-influenced `Content-Type`.
4. **CI coverage gate defaults to 60%** (`--cov-fail-under=${{ vars.HIVE_COVERAGE_MIN || 60 }}`). If the 90/100 target rubric expects meaningfully higher coverage, this default needs to be raised (via the `HIVE_COVERAGE_MIN` repo variable) rather than assumed to already be higher.

## 3. Resilience — what's real vs. what's missing

- **OpenRouter (chat/model calls):** genuine resilience — per-attempt timeout, connect timeout, stream idle timeout, first-token timeout, and a retry path for empty replies and specific failure classes (`services/openrouter.py`). `httpx.TimeoutException` and `httpx.HTTPError` are both caught and converted into structured retryable-error payloads rather than propagating as raw 500s.
- **R2 dependency probing:** `services/dependency_readiness.py` runs bounded, cached, read-only probes against required R2 lanes and feeds `/readyz`'s 503 behavior. Exceptions are caught narrowly (`RuntimeError, ValueError, OSError`), not blanket `except Exception`.
- **AIMS / RAMS / MAST:** live, timeout-bounded HTTP health probing exists and is real — `services/repo_health.py` uses a shared `httpx.AsyncClient` with an explicit timeout and connection limits, catches `httpx.TimeoutException` and `httpx.HTTPError` separately, and redacts provider error detail before it reaches the response. This is exposed authenticated at `/v1/system/repo-health`.
- **Gap:** I could not find or execute any test that actually simulates AIMS, RAMS, R2, or OpenRouter being *down* end-to-end (i.e., mocking the transport to raise `ConnectError`/`TimeoutException` and asserting the API still returns a coherent degraded response rather than a 500). The building blocks for graceful degradation are there in the code; there just isn't an automated test proving it, and I had no network in this environment to install `pytest`/`httpx`/`respx` and write+run one safely. **This is the single biggest thing to close before trusting "resilience" as verified rather than "resilience by inspection."**

## 4. Ecosystem validation (MAST / AIMS / RAMS)

- Config wiring for AIMS/RAMS health and readiness URLs, and RAMS QA-event ingestion, is present and consistent between `.env` templates, `core/config.py`, and `ci.yml`'s preflight step.
- `services/ecosystem_index.py`'s `/ecosystem/status` is configuration-only (does it exist/is it turned on), not a live probe — that's a different, complementary thing from `repo_health.py`'s live probing, and the two shouldn't be confused. No change needed, just noting the distinction for anyone reading the code later.
- Retry logic for outbound MAST/AIMS/RAMS calls: timeouts exist; I did not find exponential backoff or multi-attempt retry on these specific probes (single attempt, bounded timeout, then reported as down). For a *health check* that's arguably correct behavior (a health check shouldn't retry-loop), but if RAMS QA-event delivery ever becomes something HIVE pushes rather than receives, that path would need its own retry policy.

## 5. Operational readiness

- Three-tier health surface, correctly separated by audience:
  - `/livez` — process liveness, public, minimal.
  - `/readyz` — configuration + dependency readiness, public, redacted, correct 503 on failure.
  - `/v1/runtime/readiness` — full detail, authenticated.
  - `/health` — see finding #1 above; sits awkwardly between these tiers.
- Structured logging is real: every request gets a request ID (validated against a strict pattern or regenerated), method/path/status/duration are logged, and the request ID is also returned in `X-Request-ID` and included in the unhandled-exception handler's response — so a report from a user can be traced to a log line.
- Security headers (CSP, X-Frame-Options, HSTS in production, Permissions-Policy, COOP/CORP) are applied centrally in `ProductionMiddleware`, not per-route, so new routes inherit them automatically.
- CI already blocks merges on: pytest with a coverage floor, ruff, mypy, bandit, pip-audit, and a production-config preflight script run with realistic (fake) production env vars. This is a strong CI gate set already in place — nothing further needed structurally, only the coverage threshold value (see finding #4).

## 6. Repository cleanup performed

- 17 patch/env `.txt`/`.md` files at the repo root were byte-identical duplicates of files already tracked in `docs/releases/` (leftover copies from when each patch was applied). Verified identical via `diff`, then removed the root copies — nothing was deleted that doesn't already exist in `docs/releases/`.
- Moved into `docs/releases/`: `HIVE-PATCH-MANIFEST.txt`, `HIVE-REPO-HEALTH-KOYEB-ENV-PATCH.txt`, `FROZEN_FILES_SHA256.txt`.
- Moved into `docs/reports/`: `HIVE-PRODUCTION-READINESS-REPORT.md` (this file's predecessor, itself already labeled "Historical implementation record" at the top).
- `HIVE-KOYEB-PRODUCTION-ENV.txt` was a pre-env-split monolithic env dump, superseded by `HIVE-PRODUCTION-SHARED.env` + `HIVE-KOYEB-SECRETS-ONLY.env` (confirmed near-identical key sets). Moved to `docs/releases/HIVE-KOYEB-PRODUCTION-ENV.superseded-by-env-split.txt` rather than deleted, since it's referenced nowhere in code/CI but may still be useful history. Safe to delete outright if you don't want it.
- Verified via repo-wide grep that no `.py`, `.yml`, `.md`, `Dockerfile`, or `Procfile` referenced any of the moved files by their old root path — nothing should break.
- Root now contains only: `README.md`, `SECURITY.md`, `requirements*.txt`, `runtime.txt`, `Procfile`, `Dockerfile`, `.dockerignore`, `nixpacks.toml`, the two active env templates (`HIVE-PRODUCTION-SHARED.env`, `HIVE-KOYEB-SECRETS-ONLY.env`), plus standard dirs.

## 7. Confirmed non-issues (checked and found already solid)

- Secrets in committed `.env` templates are `{{ secret.X }}` placeholders, not resolved values — no credential leak.
- `admin_bearer_token`, CORS, and allowed-hosts all have real production fail-closed enforcement, not just documentation.
- Auth-failure rate limiting exists and is scoped correctly (IP + token-prefix, so one bad actor can't lock out a shared-NAT neighbor).
