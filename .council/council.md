# HIVE Final QA and Repository Council Prompt

## Purpose
Run the final evidence-led QA/Council pass for **HIVE** after its allocated weekend CI and DAST phases. Judge the exact default-branch SHA and do not infer green status from older evidence.

## Required evidence
- Deterministic repository CI and `ci-gate` for the target SHA.
- CodeQL and the `Trivy, Gitleaks and actionlint` security gate.
- Mergify automatic admission remains the sole routine merge authority and uses squash merge.
- Socket Security/App behaviour is consistent with `socket.yml`.
- autofix.ci only mutates code when the repository exposes a safe native fixer.
- Kilo is invoked only for classified repository-repairable failures; lint/format/provider/deployment plumbing must not wake Kilo.
- No stale `.autonomy/repair-requests/` directory, stale repair carrier, duplicate repair PR or obsolete `autonomy:human-hold`.
- OWASP ZAP DAST result for the authorised target when DAST_ENABLED=true; if disabled, report DAST_DISABLED rather than inventing a pass.
- Exact deployment verification through Koyeb production deployment watch.

## Required report
Return target identity, CI, security, dependency automation, repair ledger, merge evidence, deployment/readiness evidence, DAST state, remaining risks and one disposition: `READY_FOR_COUNCIL_ACCEPTANCE`, `HUMAN_HOLD`, or `NOT_READY`.

Never weaken checks, dismiss security findings, create cosmetic PRs or treat model text as proof.
