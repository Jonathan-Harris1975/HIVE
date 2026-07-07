# HIVE Remaining Risk Register — Updated 2026-07-06 (follow-up pass)

Supersedes the same-day initial risk register. Status column reflects this pass.

| # | Risk | Severity | Status |
|---|------|----------|--------|
| 1 | No automated test simulated AIMS/RAMS/R2/OpenRouter outages. | High | **Closed.** Added `backend/tests/test_resilience_outage_simulation.py` — 10 tests forcing real transport-level failures (`httpx.ConnectError`/`ConnectTimeout`, `OSError`) against the actual probe/client functions and asserting graceful structured degradation, not exceptions. Not executed live in this environment (no network to install pytest) — verify on next CI run. |
| 2 | `/health` leaked integration/config detail unauthenticated. | Medium | **Closed.** Removed redundant flat duplicate keys and stripped dialect/provider/model/dimensions/index_name detail from `storage_flags`. Verified against every existing test's exact assertions first — nothing depended on the removed fields. |
| 3 | CI coverage gate defaulted to 60%. | Medium | **Improved.** Raised to 72 (comfortably below the measured 76.07% actual, so this shouldn't newly break the build). Recommend raising further once the new resilience tests' coverage contribution is known from a real run. |
| 4 | Upload endpoints trust caller-supplied `content_type`. | Low | **Open, unchanged.** Still admin-token-gated; deferred rather than risk an allow-list guess breaking an untested upload path. |
| 5 | Dead bypass branch in `require_admin`. | Low | **Closed.** Removed; confirmed unreachable and untested. |
| 6 | Real regression: `build_verification` QA check silently never flagged syntax errors (`py_compile(..., quiet=2)` overrides `doraise`). | **High** (newly found via CI log, not in the original register) | **Closed.** Changed to `quiet=1`; reproduced the bug and the fix locally against a real syntax-error file before shipping the change. |
| 7 | 16 tests failing on stale hardcoded version/build-stage strings. | Medium (blocks CI, not a security issue) | **Closed.** All 16 occurrences across 14 files updated to the current `BUILD_STAGE`/`APP_VERSION`. |
| 8 | mypy / bandit / pip-audit / pytest not executed in this pass. | Unknown | **Still open — inherent to this environment.** No network access here to install dependencies. Every change above was verified by direct source reading and isolated `py_compile` reproduction, not a live test run. Push and check the real CI job. |
| 9 | `HIVE-KOYEB-PRODUCTION-ENV.superseded-by-env-split.txt` still present in `docs/releases/`. | Low | **Open, unchanged** — historical artifact, not referenced anywhere, safe to delete manually whenever convenient. |
| 10 | No exponential backoff on AIMS/RAMS/MAST live health probes (single attempt, bounded timeout). | Low–Informational | **Open, unchanged** — arguably correct behavior for a health check; only relevant if RAMS/AIMS become synchronous request-path dependencies. |

## New items from this pass

- **Dependabot was not previously configured.** Added `.github/dependabot.yml` (pip + docker + github-actions, weekly). See chat response for the recommendation on whether to enable it.
