# Autonomous Repository Policy

Scheduled repository window: **Sunday 00:00 UTC**, with a 2.5-hour allocation before the next repository starts.

All changes from humans, Renovate, autofix.ci, KiloConnect/Kilo Code, RAMS/OpenRouter or future Council automation must use a pull request and pass this repository's required CI/security/deployment gates.

Kilo may diagnose and prepare code/configuration fixes, including dependency-manifest and lockfile corrections such as `package.json` changes when evidence shows they are required to complete a PR. Kilo must not merge directly to protected branches or deploy directly to production.

## Guardrails
- GREEN: deterministic formatter/lint/import/generated-file repairs may be automated.
- AMBER: application code, dependency compatibility, build/deployment configuration and ordinary bug fixes may be prepared automatically as PRs.
- RED: automation must not weaken CodeQL, Trivy, Gitleaks, tests, coverage thresholds, required checks, branch/ruleset protections, workflow permissions, secret handling, security allowlists or production access merely to obtain a green result.
- Security-scanner decisions remain independent evidence-led gates. CodeQL/repository-security failures may invoke Kilo for code repair, but alert dismissal, suppressions, allowlist changes and security-policy decisions remain human-controlled.
- Failed autonomous repairs must return through the complete repository CI/security/deployment path.

## PR lifecycle
GitHub is authoritative for PR state. Mergify housekeeping is permitted only from explicit lifecycle labels:
- `autonomy:superseded`
- `autonomy:obsolete`
- `autonomy:human-hold`

Never close a PR solely because of age. The future HIVE Repository Council will reconcile lineage and R2 history.

## Main-branch repair PR loop

If an ordinary CI/security/deployment-verification workflow genuinely fails on the default branch, or a successful CodeQL analysis reports open policy-safe findings, `.github/workflows/autonomous-repair.yml` creates one deduplicated repair **carrier PR** rather than an issue. The carrier contains an unresolved marker under `.autonomy/repair-requests/` and sends the exact `@kilocode-bot fix it` implementation command with bounded evidence, so no human reply is required to start Kilo.

Kilo GitHub implementation mode may update the carrier branch or create its own implementation branch/PR. If it updates the carrier, the marker is removed only after the underlying defect is fixed. If Kilo opens a separate implementation PR, the carrier marker stays as lifecycle evidence and a successful default-branch rerun marks the carrier obsolete. This prevents an evidence-only carrier from being mistaken for a completed repair.

Minor/digest/patch dependency automation remains owned by the committed Renovate policy. Kilo is the repair path for repository/code/configuration defects exposed by CI; it does not replace the independent CI/security gates.

Autonomous repair PRs are created with the dedicated Autonomy Repair GitHub App installation token. Configure the repository variable `AUTONOMY_REPAIR_APP_ID` and the private-key secret referenced by `.github/workflows/autonomous-repair.yml`. Branch/ruleset protections and required checks remain authoritative.

## Trusted PR creator

Autonomous repair PRs must be created with the dedicated `Autonomy Repair Bot` GitHub App installation token, not the workflow `GITHUB_TOKEN`.

GitHub deliberately places pull-request workflow runs created or updated by `GITHUB_TOKEN` into an approval-required state. Using a GitHub App installation token removes that manual approval dependency while preserving the normal target-repository CI/security gates.

The repair App has only the repository permissions needed to create the branch/PR and lifecycle labels. It does not receive merge or deployment authority.

Successful default-branch reruns automatically mark open repair PRs for the same workflow as `autonomy:obsolete` (unless they are on human hold), allowing Mergify to close stale repair carriers safely. This prevents a Kilo-created replacement PR or a manual correction from leaving the original repair PR behind.
## CodeQL and security handoff

- CodeQL analysis success and CodeQL findings are separate states. Open findings are reported by a successful **CodeQL finding triage** job and are not converted into a synthetic exit-code failure. A broken CodeQL analysis still fails normally.
- On the default branch, policy-safe open CodeQL findings enter the carrier lifecycle and are labelled `autonomy:security-repair`; protected governance/secret paths remain on `autonomy:human-hold`.
- On trusted PRs, `.github/scripts/trusted_automation.py` extracts bounded CodeQL/check evidence and automatically sends `@kilocode-bot fix it`. If Kilo Code Review itself asks for that reply, trusted automation posts it automatically; no operator reply is required.
- Genuine CI/security scan failures remain failures. Safe failed-step/check annotations can be handed to Kilo, while Gitleaks/credential/protected-policy evidence is never passed for autonomous editing and moves to `autonomy:human-hold`.
- Kilo may repair code/configuration, but it must not dismiss CodeQL alerts, weaken queries/tests, broaden suppressions, change secret allowlists or make security-policy decisions. If no safe repository code change is justified, the carrier marker stays and the work moves to `autonomy:human-hold`.

## Trusted automation admission and native merge

`.github/workflows/trusted-automation.yml` is a default-branch control plane for verified automation PRs. It never checks out or executes PR code. The installed Autonomous Repair Bot GitHub App therefore also requires **Actions: Read and write** so it can approve an `action_required` workflow run, or safely re-request the same run when GitHub requires that path. Contents, Pull requests and Issues remain read/write; Metadata is read-only.

Admission is fail-closed. Exact same-repository Mend Renovate PRs are recognised from the `renovate[bot]` identity and Renovate body marker. Kilo implementation PRs are recognised only when linked from an autonomous repair carrier or a trusted Renovate source PR that automation explicitly handed to Kilo. Unknown/unlinked bot PRs are not admitted. Fork secrets and fork write tokens remain disabled.

Renovate's committed policy remains authoritative: PRs can run CI automatically, but native auto-approval/auto-merge is requested only when the PR itself reports `Automerge: Enabled.`. Major/manual updates remain human decisions. A linked Kilo implementation PR can progress only when the current head SHA has successful repository CI, CodeQL, repository-security checks and no pending/failing current checks. Sensitive governance/security-path changes are labelled `autonomy:human-hold`.

Mergify performs lifecycle housekeeping only. Native GitHub auto-merge / Merge Queue is the final merge authority, and the trusted workflow merely requests that native path after all repository gates are green.
