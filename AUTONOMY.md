# Autonomous Repository Policy

Scheduled repository window: **Sunday 00:00–02:30 Europe/London** (the Renovate schedule in `renovate.json`), a 2.5-hour allocation before the next repository starts.

## Weekend orchestration
`.github/workflows/weekend-orchestrator.yml` runs once, straight after the Renovate window closes (02:30 London; two UTC crons plus a London-time guard cover BST and GMT). It is the single control plane for the weekend run:
1. **Settle.** Wait until Mergify has completed: no automation PR (admitted, queued, repair, human-hold, Renovate auto-merge) is open and no PR run is in flight. Idle admitted PRs get one `@mergifyio refresh`; trusted-automation is nudged every 15 minutes. Limit: 3 hours, then `HUMAN_HOLD`.
2. **Verify.** Dispatch CI, CodeQL and Security on the settled default-branch SHA, then confirm the exact-SHA Koyeb deployment. CodeQL, Security, Lychee, Scorecard and the Codecov upload run here, not on every push or PR.
3. **Repair.** A failure goes through the existing carrier-PR and Kilo path. Once the repair has merged, the orchestrator settles and verifies again (two repair attempts at most, then `HUMAN_HOLD`).
4. **Council.** Released only when everything above is green, no repair or human-hold PR is open, and it is at least one hour after the window closed (03:30 London). Renovate updates still under the two-day release age are reported to the Council as `PENDING_MINIMUM_AGE` and roll into the next weekend automatically.

A manual run defaults to a dry run (everything except releasing the Council) with the time guard skipped; use it to prove the sequence before the first live weekend.

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

If a genuine CI, security or deployment verification run fails on the default branch, `.github/workflows/autonomous-repair.yml` creates one deduplicated marker-bearing repair carrier PR and calls the configured Kilo Cloud Agent webhook. A bot-authored `@kilocode-bot` comment is not an implementation trigger. Kilo opens one implementation PR linked to the carrier; the marker prevents an evidence-only carrier from merging.

On an existing same-repository PR, `.github/workflows/pr-issue-repair.yml` extracts failing job/step names, bounded high/critical CodeQL alert identifiers on added PR lines and actionable Kilo review comments, then calls the same webhook without a human reply. It does not execute the PR's code or download scan logs. Attempts are bounded per PR head and type. Kilo's implementation PR must link the source PR and contain its current head commit; the trusted admission workflow verifies the exact-head receipt and ancestry before admitting it.

Minor/digest/patch dependency automation remains owned by the committed Renovate policy. Kilo is the repair path for repository/code/configuration defects exposed by CI; it does not replace the independent CI/security gates.

Autonomous repair PRs are created with the dedicated Autonomy Repair GitHub App installation token. Configure the repository variable `AUTONOMY_REPAIR_APP_ID` and the private-key secret referenced by `.github/workflows/autonomous-repair.yml`. Branch/ruleset protections and required checks remain authoritative.

## Trusted PR creator

The autonomous repair workflow must use the dedicated Autonomy Repair GitHub App installation token rather than `GITHUB_TOKEN`. GitHub deliberately requires manual approval for pull-request workflows created or updated by `GITHUB_TOKEN`; App-created PRs avoid that manual approval path while retaining the repository's normal checks.

The repair App receives the repository permissions needed for repair branches/PRs/lifecycle labels plus Actions read/write for trusted workflow admission. It may admit eligible PRs to Mergify after exact-head gates pass, but it cannot bypass GitHub branch/ruleset protections and receives no deployment, secrets, administration or security-event write authority.

Successful default-branch reruns automatically mark open repair PRs for the same workflow as `autonomy:obsolete` (unless they are on human hold), allowing Mergify to close stale repair carriers safely. This prevents a Kilo-created replacement PR or a manual correction from leaving the original repair PR behind.
## CodeQL and security handoff

- The additional CodeQL gate fails only for open, high/critical CodeQL security alerts. On PRs it requires an alert on an added line; GitHub's separately configured code-scanning ruleset remains authoritative for other introduced findings. Existing alerts and review handoff are not CI errors by themselves.
- Trivy and Gitleaks findings remain blocking. Lychee link checks and historical OpenSSF Scorecard observations are advisory, so transient external links or baseline scores do not misclassify a PR as a security failure.
- A genuine failed CodeQL/security check on a PR routes the failed job/step names to Kilo. Secrets and raw scanner output are never sent to the agent.
- Kilo may repair code/configuration, but it must not dismiss CodeQL alerts, weaken queries/tests, broaden suppressions, change secret allowlists or make security-policy decisions. If no safe repository code change is justified, the carrier marker stays and the work moves to `autonomy:human-hold`.

## Trusted automation admission for Mergify

`.github/workflows/trusted-automation.yml` is a default-branch control plane for verified automation PRs. It never checks out or executes PR code. The installed Autonomous Repair Bot GitHub App therefore also requires **Actions: Read and write** so it can approve an `action_required` workflow run, or safely re-request the same run when GitHub requires that path. Contents, Pull requests and Issues remain read/write; Metadata is read-only.

Admission is fail-closed. Exact same-repository Mend Renovate PRs are recognised from the `renovate[bot]` identity and Renovate body marker. Kilo implementation PRs require the configured creator login and either an active App-authored carrier link or a same-repository source PR link with an exact-head router receipt and preserved source ancestry. Unknown/unlinked bot PRs are not admitted. Fork secrets and fork write tokens remain disabled.

Renovate's committed policy remains authoritative: PRs can run CI automatically, but trusted approval/Mergify admission is requested only when Renovate applies `dependency:auto-eligible`. Manual classes carry `dependency:manual` and remain human decisions. Renovate itself never queues or merges the PR. A linked Kilo implementation PR can progress only when the current head SHA has successful repository CI, CodeQL, repository-security checks while GitHub enforces other required ruleset checks. Sensitive governance/security-path changes are labelled `autonomy:human-hold`; carrier PRs never auto-merge.

Mergify Merge Queue is the sole routine merge authority. The trusted workflow admits eligible PRs only after exact-head CI/security verification. Mergify queues them, reruns the required candidate checks, and squash merges them. GitHub native Merge Queue is not used. Verified obsolete/superseded repair carriers are closed directly by the repair lifecycle workflows; Mergify closure rules remain a fallback.
