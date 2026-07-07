# HIVE Production Hardening — Follow-Up Pass

**Date:** 2026-07-06 (follow-up to the same-day initial hardening report)
**Trigger:** Actual GitHub Actions CI run (`logs_77782049697`) showing the backend test job failing: 17 failed, 269 passed, coverage 76.07%.
**Scope:** Fix the real CI failures, then act on every open recommendation from the initial report/risk register/score that could be done safely without live test execution in this environment (still no network access here — see caveat at the end).

## What the CI log actually showed

Two distinct failure classes, not one:

1. **16 tests failing on a stale version string.** `BUILD_STAGE` in `core/version.py` and `APP_VERSION` in `HIVE-PRODUCTION-SHARED.env` are correctly at `v1.30-repository-qa-through-documentation` / `1.30-production` — the *tests* still hardcoded the much older `v1.26.12-catalogue-metadata` / `1.26.12-production` from several releases back. This is a maintenance gap (the version marker was bumped across v1.27→v1.30 without updating the tests that pin it), not an application bug.
2. **1 real regression:** `test_v133_repository_qa.py::test_build_verification_flags_python_syntax_errors` — the repository-QA "build_verification" check was supposed to flag a file with a Python syntax error as `"warning"`, but returned `"ok"` instead.

## Fixes applied

### 1. Real bug: `repository_qa.py` build_verification check was silently disabled

`_check_build_verification` called `py_compile.compile(..., doraise=True, quiet=2)`. In Python's `py_compile` module, `quiet=2` unconditionally skips raising the exception — it overrides `doraise` entirely. So **every syntax error in every ingested repository was being silently ignored** by this check; it always reported `"ok"`. Verified this directly against a locally reproduced syntax-error file: `quiet=2` returns with no exception even with `doraise=True`; `quiet=1` (or `0`) correctly raises `py_compile.PyCompileError`.

Fix: changed `quiet=2` → `quiet=1` (still suppresses the printed traceback, but restores `doraise`). This is a one-line fix with real production impact — anyone relying on the QA report to catch broken code in an ingested repository was getting a false "ok."

### 2. Stale version-string assertions (16 occurrences, 14 files)

Updated every hardcoded `"v1.26.12-catalogue-metadata"` → `"v1.30-repository-qa-through-documentation"` and `"1.26.12-production"` → `"1.30-production"` to match what's actually shipped. Files touched: `test_env_split.py`, `test_health_operational.py`, `test_v113_repo_hygiene.py`, `test_v114_execution_review_queue.py`, `test_v115_review_evidence_pack.py`, `test_v117_registry_integrity.py`, `test_v119_workflow_graphs.py`, `test_v120_to_v122_execution_preview_persistence.py`, `test_v15_ingestion_expansion.py`, `test_v16_workflow_presets_r2_lanes.py`, `test_v17_ecosystem_intelligence.py`, `test_v18_skill_registry_import.py`, `test_v19_to_v112_skill_intelligence.py`, `test_vectorize_foundation.py`.

Every changed line was verified individually after the edit; no unintended matches were touched.

## Recommendations from the earlier risk register now actioned

### Trimmed `/health` disclosure (risk register #2)
Removed the redundant top-level flat keys (`r2_configured`, `openrouter_configured`, `vectorize_configured`, `vectorize_enabled`, `embeddings_configured`, `embeddings_enabled`, `database_configured`, `database_enabled`, `database_dialect`, `d1_configured`, `d1_enabled`) and the full `execution_adapter_policy` dict, which duplicated what's already nested (as booleans) under `storage_flags`. Also stripped `dialect`, `provider`, `model`, `dimensions`, and `index_name` from inside `storage_flags` itself — the endpoint now reports only enabled/configured booleans, plus the already-tested R2 lane-name list, never provider/model/dialect specifics. Confirmed by grepping every test in the suite for these exact key names first — nothing depended on them, so nothing should break.

### Removed dead code in `require_admin` (risk register #5)
Deleted the unreachable `if request.url.path == "/health": return` bypass — `health.py` never applies `require_admin` as a dependency, so this branch could never fire. No test referenced it.

### Raised CI coverage floor (risk register #3)
`--cov-fail-under` default raised from 60 → 72 in `ci.yml`. Chosen to sit comfortably below the measured actual (76.07%) so this doesn't newly break the build, while meaningfully raising the enforced bar. Recommend nudging this up further (e.g., to 78–80) once the new resilience tests below have run once and their exact contribution to coverage is known.

### Added outage-simulation tests (risk register #1 — the biggest gap)
New file: `backend/tests/test_resilience_outage_simulation.py`. Ten tests, all injecting real transport-level failures (not just mocking the business logic away) and asserting graceful degradation:

- **AIMS/RAMS/MAST-style probes** (`services/repo_health.py`): `httpx.MockTransport` forces `ConnectError` and `ConnectTimeout` against `_probe_target`, asserting the result is `status: "down"` rather than a raised exception; a fourth test confirms an unconfigured target reports `"not_configured"` without ever touching the transport.
- **R2** (`services/dependency_readiness.py`): monkeypatches `R2Storage.list_objects_page` to raise `OSError`, asserting the dependency-readiness report surfaces a named `"error"` probe and flips `ready` to `False` — plus a paired sanity test confirming the same lane reports `"ok"` when the probe succeeds, so the negative test isn't accidentally vacuous.
- **OpenRouter** (`services/openrouter.py`): monkeypatches `httpx.AsyncClient.post` to raise `ConnectError` and `ConnectTimeout` directly against `_post_json`, asserting the retryable structured-error payload (502 / 408) rather than a raised exception; a further end-to-end test drives this through the public `chat_completion()` method and asserts `_all_attempts_failed: True` is returned instead of the exception propagating to the API layer. The OpenRouter model-preflight is explicitly disabled in these tests' settings so they don't attempt a real network call to list models.

These were written by reading the exact source of each function (not guessed), and every field name, alias, and exception hierarchy relationship used was independently verified against the actual code before being relied on. I was not able to execute `pytest` myself in this environment (no network to install dependencies) — see the caveat below.

### Dependabot
Added `.github/dependabot.yml` covering `pip` (requirements.txt/requirements-dev.txt), `docker` (the Dockerfile's base images), and `github-actions` (the workflow files), all on a weekly schedule with patch/minor grouping for Python to keep PR volume manageable. See the separate note in the chat response for why this is worth turning on.

## What's deliberately not changed

- Upload `content_type` allow-listing (risk register #4) — still low severity, and every `files.py` route already requires the admin bearer token. Left alone rather than guessing at an allow-list that might reject a legitimate type currently exercised by existing tests I can't run.
- The stale/superseded `HIVE-KOYEB-PRODUCTION-ENV.superseded-by-env-split.txt` in `docs/releases/` — untouched, as before.

## Caveat (unchanged from the initial report)

This sandbox still has no outbound network access, so none of `pytest`, `mypy`, `bandit`, or `pip-audit` could be run here. Every fix above was verified by direct code reading, targeted `py_compile` checks, cross-referencing every test assertion against the exact current source, and reproducing the `quiet=2` bug in isolation with a local Python interpreter — but "the CI will now go green" is a strong expectation from that analysis, not a guarantee from an actual run. Push this and watch the real CI job; if anything above was subtly wrong, that run will show exactly which assertion.
