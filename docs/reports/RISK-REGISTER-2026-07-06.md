# HIVE Remaining Risk Register

**Date:** 2026-07-06
Ordered roughly by severity × effort-to-fix. "Verified" means confirmed by reading source in this repo; "Unverified" means it depends on a live run (network/tests) that wasn't possible in this environment.

| # | Risk | Severity | Status | Recommendation |
|---|------|----------|--------|-----------------|
| 1 | No automated test simulates AIMS/RAMS/R2/OpenRouter being unreachable end-to-end and asserts the API degrades gracefully rather than 500ing. | High | Verified gap (absence confirmed by searching `backend/tests`) | Add `respx`/`httpx.MockTransport`-based tests that force `ConnectError`/`TimeoutException` on each outbound client and assert on the resulting status code and payload shape. This is the one thing standing between "resilience by code inspection" and "resilience proven." |
| 2 | `/health` leaks integration/config details (which services are enabled, DB dialect, embeddings provider/model) without authentication. | Medium | Verified | Trim `/health` to match `/healthz`'s minimalism; move detail to the already-authenticated `/v1/runtime/readiness`. |
| 3 | CI coverage gate defaults to 60% (`HIVE_COVERAGE_MIN` unset). | Medium | Verified | Set the `HIVE_COVERAGE_MIN` repository variable to whatever the 90/100 rubric actually requires; don't rely on the code default. |
| 4 | Direct upload endpoints trust caller-supplied `content_type` with no allow-list or sniffing. | Low | Verified | Add a small allow-list check in `files.py`; low risk today since the whole router requires the admin bearer token. |
| 5 | `require_admin` contains a dead `/health` bypass branch that is never reached (health.py doesn't use this dependency). | Low | Verified | Delete the dead branch so it can't be mistaken for an intentional bypass later. |
| 6 | `HIVE-KOYEB-PRODUCTION-ENV.superseded-by-env-split.txt` (moved to `docs/releases/`) still exists and could be copy-pasted by mistake instead of the current split files. | Low | Verified | Either delete it outright or add a one-line header marking it superseded (not done automatically here to avoid altering a historical artifact without sign-off). |
| 7 | mypy / bandit / pip-audit / pytest were not executed in this pass (no network in this sandbox to install dependencies). | Unknown — could hide real issues | Unverified | Run `pytest --cov`, `mypy backend/app`, `bandit -r backend/app -ll`, and `pip-audit -r requirements.txt` locally or in CI and treat this report as a static-review supplement, not a replacement, for that run. |
| 8 | No exponential backoff / multi-attempt retry on AIMS/RAMS/MAST live health probes (single attempt, bounded timeout). | Low–Informational | Verified | Likely correct as-is for a *health check* (retry-looping a health check adds latency without much benefit), but flag if RAMS/AIMS ever become synchronous dependencies of a request path rather than just probes. |

## Not risks (confirmed solid, listed so they aren't re-litigated)

- Admin bearer token, CORS origin, and allowed-hosts wildcard all fail closed in production via `enforce_production_readiness`, called from app startup.
- Request body size limits enforced both via `Content-Length` and streamed byte counting.
- Zip extraction has real member-count/size/depth/total-text bounds (not just outer-file size).
- Auth-failure rate limiting is real, in-process, IP+token scoped, with sliding window and lockout.
- Committed `.env` templates contain only secret placeholders, never resolved values.
