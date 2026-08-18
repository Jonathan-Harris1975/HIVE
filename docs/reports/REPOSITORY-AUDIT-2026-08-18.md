# HIVE Repository Audit and Remediation Report

**Audit date:** 18 August 2026  
**Scope:** code quality, documentation, testing, dependency management and security  
**Repository:** HIVE

## Executive summary

The repository has a solid production baseline rather than a neglected one. It already contained CI, a compiled dependency set, Dependabot configuration, security and deployment documentation, repository-hygiene checks, and a broad automated test suite. The audit examined 171 Python files across the backend and scripts, with 9,465 executable application statements measured by coverage and 56 test modules before the additional remediation tests were added.

The most important finding was an authentication-throttling weakness. Failed bearer-token attempts were grouped by source IP plus the first eight characters of the supplied token. An attacker could therefore rotate those first characters and continually create fresh rate-limit buckets. The same area also trusted forwarded-client information too broadly because the production startup configuration set Uvicorn's `FORWARDED_ALLOW_IPS` to `*`. A separate development convenience allowed the sentinel token when the application was in a development-like environment without independently requiring a loopback peer. None of these findings proves compromise occurred; they were weaknesses in the defensive boundary.

A second material finding was dependency exposure. The compiled set used Starlette 0.50.0. The 2026 advisory GHSA-82w8-qh3p-5jfq affects Starlette versions below 1.3.1 and concerns denial of service in URL-encoded form parsing. The repository was therefore inside the affected range. FastAPI was also behind its current release. The remediation pins FastAPI 0.141.1 and Starlette 1.6.0, both current releases verified against their package metadata during the audit.

After remediation, the complete local suite passes: **336 tests passed**, with **74.53% total coverage**, above the newly raised 74% CI floor. No syntax errors were found. A heuristic credential scan found no apparent live GitHub, OpenRouter, private-key, or similar credentials. One AWS-shaped key is deliberately present in a security-scanner test fixture and is not a production credential.

## Detailed analysis

### 1. Authentication and proxy handling — high priority

**Observed issue.** `backend/app/core/rate_limit.py` originally identified a failed credential by `(IP address, first eight token characters)`. This made the per-credential limiter easy to sidestep by changing the guessed prefix. In addition, raw proxy trust was effectively universal because `scripts/start.sh`, `.env.example`, and `HIVE-PRODUCTION-SHARED.env` used `FORWARDED_ALLOW_IPS=*`. Uvicorn documents `*` as trusting every incoming peer to supply proxy headers. Koyeb, meanwhile, documents a more specific rule: it appends the connecting client IP to `X-Forwarded-For` and certifies the final value.

**Remediation.** The limiter now uses a truncated SHA-256 token fingerprint, so no literal bearer-token prefix is retained. It enforces both a tight `(source IP, token fingerprint)` bucket and a broader source-IP bucket, closing the rotating-prefix bypass. The development sentinel bypass now requires a loopback peer, with a narrow allowance for the controlled FastAPI test client. Uvicorn proxy trust now defaults to `127.0.0.1`, not `*`. When `KOYEB_PUBLIC_DOMAIN` is present, the authentication limiter validates and uses only the final Koyeb `X-Forwarded-For` address. Regression tests cover rotating guesses, fingerprint privacy, invalid forwarded values, Koyeb last-hop selection and loopback-only development access.

### 2. Dependency management and vulnerability exposure — high priority

**Observed issue.** The direct dependency file and compiled set were internally organised, but the Starlette 0.50.0 pin fell within a published 2026 vulnerability range. The production Docker and Nixpacks install paths also upgraded `pip` during installation, making production builds slightly less reproducible because the installer itself could change independently of the reviewed repository.

**Remediation.** FastAPI is pinned to 0.141.1 and Starlette to 1.6.0 in `requirements.in`/`requirements.txt`, with matching minimums in `backend/pyproject.toml`. Package metadata confirms FastAPI 0.141.1 accepts Starlette versions from 0.46.0 upward, while Starlette 1.6.0's required dependencies are satisfied by the existing compiled set. `pip-tools` is now part of the development toolchain, `pip check` runs in every CI test matrix job, and production install paths no longer upgrade `pip` opportunistically. Dependabot already covers pip, Docker and GitHub Actions weekly and has been retained.

### 3. Runtime and CI consistency — medium priority

**Observed issue.** The repository had three Python stories: Nixpacks/runtime files pinned 3.11, CI tested 3.11 and 3.12, the dependency file had been compiled under 3.13, and the Docker image used Python 3.14. This was not evidence of a present failure, but it left the actual production Docker runtime outside the test matrix.

**Remediation.** Package metadata now declares Python `>=3.11,<3.15`, and CI tests 3.11, 3.12, 3.13 and 3.14. The fallback remains deliberately on 3.11.15 and Docker on 3.14.6, but both ends are now represented by CI. The test job also performs `pip check`, and Ruff now includes `backend/tests` rather than omitting test code. The coverage threshold was raised from 72% to 74%.

### 4. Testing depth — medium priority

**Observed issue.** Overall coverage was respectable but uneven. Before remediation, the suite contained 324 passing tests at 74.33% coverage. Several operationally important modules remained lightly exercised: `services/koyeb_control.py` and `services/repository_pipeline.py` at 19%, `storage/d1.py` at 21%, `storage/vectorize.py` at 27%, `services/service_lifecycle.py` at 28%, `api/repositories.py` at 30%, and `api/service_actions.py` at 35%.

**Remediation.** Twelve focused regression tests were added, taking the suite to 336 tests. Authentication controls now have direct adversarial tests, the production environment split asserts that universal proxy trust cannot silently return, and `core/text_safety.py` moved from 43% to 100% coverage with recursive NUL-normalisation tests. The final total is 74.53%. The remaining low-coverage modules should be the next testing tranche, preferably with mocked Cloudflare/Koyeb boundaries so tests stay deterministic rather than calling live services.

### 5. Maintainability and code structure — medium priority

**Observed issue.** Several modules have accumulated too many responsibilities. `api/files.py` is 2,773 lines, `services/skill_registry.py` 1,428, `storage/sql_store.py` 1,378 and `core/config.py` 1,074. Eighteen functions are at least 100 lines; `chat_with_file` alone is 386 lines. This is maintainability debt, not proof of incorrect behaviour, but it increases the cost of safe change and makes targeted testing harder.

**Remediation.** I did not perform a sweeping refactor during a security and quality audit because that would enlarge the regression surface without a feature requirement. Instead, `CONTRIBUTING.md` now makes extraction of cohesive helpers the preferred pattern and names the highest-priority modules. The sensible next sequence is tests first, then incremental decomposition of file ingestion/chat, skill registry, SQL persistence and configuration assembly.

### 6. Documentation and repository hygiene — medium/low priority

**Observed issue.** Documentation was extensive but had drift. The README and security/deployment pages had stale review dates and incomplete local quality commands. `docs/production-readiness.md` referred to a `requirements.lock` file that does not exist. The changelog contained multiple top-level titles, and there was no contributor workflow describing the authoritative dependency and quality-gate process. `.gitignore` covered `.env` but not the broader `.env.*` family or common private-key containers. Several retained historical release snapshots also contain superseded production settings, so they needed a clearer boundary from active guidance.

**Remediation.** README, SECURITY, production-readiness and Koyeb deployment documents were refreshed; the nonexistent lockfile reference was removed; the changelog now has one canonical heading and a dated audit entry; and `CONTRIBUTING.md` documents supported runtimes, quality commands, dependency regeneration and secret handling. `docs/releases/README.md` now labels old release artefacts as historical and points operators to the current configuration sources. `.gitignore` now excludes `.env.*`, `*.pem`, `*.key` and `*.p12` while explicitly preserving `.env.example`.

## Remediation plan and verification

The immediate remediation is complete in the supplied repository. Source changes compile successfully, the full test suite passes, and the raised coverage gate passes. The repository's CI remains responsible for executing Ruff, Mypy, Bandit, `pip-audit`, the four-version Python matrix and Docker smoke tests in a network-enabled runner. Those tools could not all be executed in this audit container because Ruff/Mypy/Bandit/pip-audit were not installed, outbound package resolution was unavailable, and Docker was not present. Importantly, the local host also did not contain the newly pinned FastAPI/Starlette releases, so compatibility of those exact installed wheels must be confirmed by CI; their dependency metadata was checked and is compatible on paper.

## Conclusion and next steps

HIVE leaves this pass with its most concrete security weaknesses corrected, dependency policy tightened, runtime coverage aligned with deployment reality, stronger regression tests, and clearer operating documentation. The remaining work is chiefly structural rather than emergency repair. The next highest-value actions are to let CI validate the upgraded dependency set and container, then increase tests around Koyeb lifecycle control, repository pipelines, D1/Vectorize and service-action endpoints before decomposing the largest modules. That sequence reduces technical debt without turning maintenance into a demolition derby.
