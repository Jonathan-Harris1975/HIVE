# Autonomous Repository Policy

Scheduled repository window: **Sunday 00:00 UTC**, with a 2.5-hour allocation before the next repository starts.

All changes from humans, Renovate, autofix.ci, KiloConnect/Kilo Code, RAMS/OpenRouter or future Council automation must use a pull request and pass this repository's required CI/security/deployment gates.

Kilo may diagnose and prepare code/configuration fixes, including dependency-manifest and lockfile corrections such as `package.json` changes when evidence shows they are required to complete a PR. Kilo must not merge directly to protected branches or deploy directly to production.

## Guardrails
- GREEN: deterministic formatter/lint/import/generated-file repairs may be automated.
- AMBER: application code, dependency compatibility, build/deployment configuration and ordinary bug fixes may be prepared automatically as PRs.
- RED: automation must not weaken CodeQL, Trivy, Gitleaks, tests, coverage thresholds, required checks, branch/ruleset protections, workflow permissions, secret handling, security allowlists or production access merely to obtain a green result.
- Security-scanner failures are excluded from automatic Kilo invocation and remain independent evidence-led gates.
- Failed autonomous repairs must return through the complete repository CI/security/deployment path.

## PR lifecycle
GitHub is authoritative for PR state. Mergify housekeeping is permitted only from explicit lifecycle labels:
- `autonomy:superseded`
- `autonomy:obsolete`
- `autonomy:human-hold`

Never close a PR solely because of age. The future HIVE Repository Council will reconcile lineage and R2 history.

## Main-branch repair PR loop

If an ordinary CI or deployment-verification workflow fails on the default branch, `.github/workflows/autonomous-repair.yml` creates one deduplicated repair PR rather than an issue. The PR contains an unresolved marker under `.autonomy/repair-requests/` and asks `@kilocode-bot` to diagnose and implement the smallest safe correction in PR context.

The marker must be removed only after the underlying defect is fixed. This prevents the placeholder PR from being mistaken for a completed repair.

Minor/digest/patch dependency automation remains owned by the committed Renovate policy. Kilo is the repair path for repository/code/configuration defects exposed by CI; it does not replace the independent CI/security gates.

Autonomous repair PRs are created with the dedicated Autonomy Repair GitHub App installation token. Configure the repository variable `AUTONOMY_REPAIR_APP_ID` and the private-key secret referenced by `.github/workflows/autonomous-repair.yml`. Branch/ruleset protections and required checks remain authoritative.

## Trusted PR creator

Autonomous repair PRs must be created with the dedicated `Autonomy Repair Bot` GitHub App installation token, not the workflow `GITHUB_TOKEN`.

GitHub deliberately places pull-request workflow runs created or updated by `GITHUB_TOKEN` into an approval-required state. Using a GitHub App installation token removes that manual approval dependency while preserving the normal target-repository CI/security gates.

The repair App has only the repository permissions needed to create the branch/PR and lifecycle labels. It does not receive merge or deployment authority.

Successful default-branch reruns automatically mark open repair PRs for the same workflow as `autonomy:obsolete` (unless they are on human hold), allowing Mergify to close stale repair carriers safely. This prevents a Kilo-created replacement PR or a manual correction from leaving the original repair PR behind.

