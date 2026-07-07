# HIVE Production Readiness Score — Updated 2026-07-06 (follow-up pass)

## Overall: 92 / 100 (projected — pending confirmation from a real green CI run)

I could not execute `pytest`/`mypy`/`bandit`/`pip-audit` myself in this environment (no network access to install dependencies). This score reflects what the code and CI config now say, verified by direct reading and isolated reproduction of the one real bug found — not a live test run. Push this and check the actual CI job before treating 92 as confirmed rather than projected.

| Category | Score | Change | Why |
|---|---|---|---|
| Test coverage | 17 / 20 | +5 | Coverage floor raised 60%→72%. A real, previously-undetected regression (silently-disabled syntax-error detection) was found and fixed — evidence the suite's assumptions are now being checked more carefully, not just that the number went up. 10 new outage-simulation tests close the biggest previously-flagged gap. Not docked further only because I couldn't personally confirm a green run. |
| Security hardening | 19 / 20 | +2 | `/health` no longer discloses DB dialect, embeddings provider/model/dimensions, or duplicate flat config flags to unauthenticated callers. Dead bypass code removed. Remaining point held back only for the still-open, low-severity upload `content_type` allow-list item. |
| CI enforcement | 19 / 20 | 0 | Already strong (pytest+coverage, ruff, mypy, bandit, pip-audit, production preflight, Docker build). Coverage threshold is now meaningfully enforced rather than a rubber-stamp 60%. Held at 19 pending an actual observed green run with the new tests included. |
| Reliability / resilience | 18 / 20 | +4 | The single biggest gap from the initial pass — no proof of graceful degradation — is now backed by 10 tests injecting real transport failures against the actual AIMS/RAMS/MAST probe function, the R2 dependency-readiness prober, and the OpenRouter client. Docked 2 points: these weren't executed by me in this session, and MAST's more complex durable-R2-state path and upload content-type validation remain untested/unaddressed. |
| Operational readiness / documentation | 19 / 20 | +2 | `/health` now cleanly fits the same "booleans only, no secrets" discipline as `/healthz` and `/readyz`, closing the one inconsistency flagged earlier. Everything else from the initial pass (tiered health endpoints, structured logging, security headers, clean repo root) stands. |

## To close the remaining gap to ~98

1. **Actually run the suite** (`pytest --cov`, `mypy backend/app`, `bandit -r backend/app -ll`, `pip-audit -r requirements.txt`) and fix anything the new tests or raised coverage floor surface that wasn't visible from static review.
2. Add an allow-list for upload `content_type` in `files.py` (the one remaining open finding from the original audit).
3. Once coverage from a real run is known, consider raising `HIVE_COVERAGE_MIN` again if there's comfortable headroom above 72%.
4. Consider a MAST-specific outage test against `_probe_mast_worker`'s durable-R2-state path (not just the simpler HTTP-probe path covered here), since MAST's liveness check works differently from AIMS/RAMS.
