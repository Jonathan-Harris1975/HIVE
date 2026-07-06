# HIVE Production Readiness Score — 2026-07-06

## Overall: 79 / 100

Scored against the five rubric categories, weighted equally (20 pts each). Based on static code review only — see the constraint note in the hardening report regarding no live test execution.

| Category | Score | Why |
|---|---|---|
| Test coverage | 12 / 20 | CI enforces a coverage floor, but it defaults to 60% and there's no test suite for dependency-outage simulation (AIMS/RAMS/R2/OpenRouter down). Existing unit tests are broad in feature coverage (52 test files spanning most version increments) but thin specifically on failure-path behavior. |
| Security hardening | 17 / 20 | Auth applied consistently at the router level almost everywhere, constant-time token comparison, real auth-failure rate limiting, fail-closed production config checks, zip-bomb bounds, double-enforced body size limits, strong security headers. Docked for the `/health` info-disclosure gap and unvalidated upload `content_type`. |
| CI enforcement | 19 / 20 | pytest+coverage, ruff, mypy, bandit, pip-audit, a production-config preflight script with realistic fake prod env, and a Docker build check, all as blocking gates on push/PR. About as complete as this category gets. Docked one point only because the coverage floor itself (60%) is set low relative to the other gates' rigor. |
| Reliability / resilience | 14 / 20 | Timeout and retry logic is real for OpenRouter; live, timeout-bounded, exception-differentiated health probing exists for AIMS/RAMS/MAST and R2. What's missing is proof — no test forces these dependencies to actually fail and asserts the API still responds sanely instead of 500ing. The building blocks are there; the verification isn't. |
| Operational readiness / documentation | 17 / 20 | Correctly tiered health endpoints (`/livez`, `/readyz`, authenticated detailed readiness), structured request logging with traceable request IDs, centrally-applied security headers, and now a clean repository root with historical patch/report files properly organized under `docs/releases/` and `docs/reports/`. Docked slightly because `/health` doesn't fit cleanly into the tiering the other three endpoints establish. |

## What would move this from 79 to 90+

1. Add outage-simulation tests for AIMS, RAMS, R2, and OpenRouter (mocked transport failures asserted against graceful API responses) — this alone is worth several points across both "test coverage" and "reliability."
2. Raise `HIVE_COVERAGE_MIN` to a value that matches the rubric's actual bar, and confirm the suite still passes at that bar (this requires a real `pytest` run, not available in this sandbox).
3. Trim `/health` to remove the unauthenticated integration-detail disclosure.
4. Run `mypy`, `bandit -ll`, and `pip-audit` locally to confirm the current baseline is clean — this report couldn't execute them due to no network access in this sandbox.
