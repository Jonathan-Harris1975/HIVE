# Kilo Code Review Guidance

Review pull requests for correctness, regressions, dependency/build consistency, security, tests and deployment safety.

For small dependency or code changes, prioritise concrete blockers over stylistic preferences. `package.json`, lockfiles, Python requirement inputs/locks and deployment configuration may be changed when required by the implementation.

For PRs labelled `autonomy:repair`, the file under `.autonomy/repair-requests/` is a deliberate unresolved marker. The repair is not complete while that marker remains. Verify that the underlying failure is genuinely fixed before accepting removal of the marker.

Carrier PRs authored by `autonomous-repair-bot[bot]` and titled `[autonomy] Repair ...` are evidence only. Never recommend merging a carrier PR; the fix belongs in a separate linked implementation PR. Close the carrier as obsolete after a successful default-branch rerun. If a carrier marker was already merged into `main`, remove only that misplaced file in a reviewed cleanup PR while keeping the failed run and carrier PR history.

Never recommend weakening CodeQL, Trivy, Gitleaks, tests, required checks, branch/ruleset protections, workflow permissions, secret handling or security policy merely to obtain a green result. Do not recommend broad suppressions or allowlists for unexplained findings.

The target repository's CI/security/deployment checks are authoritative regardless of whether the change originated from Kilo, Renovate, RAMS, another bot or a human.
## Renovate and version-control review

Treat Mend Renovate PRs as first-class autonomous maintenance work, not as incidental bot noise. Verify that the proposed version is real, manifests and lockfiles agree, generated locks remain reproducible, repository-native build/test/security checks exercise the changed dependency, and the change does not weaken deployment or runtime contracts.

Routine patch, minor, digest, vulnerability and lock-maintenance updates may proceed through the normal autonomous admission path when all required checks pass. Do not require a human review merely because Renovate authored the PR. Do not bypass `ci-gate`, CodeQL, Trivy/Gitleaks/actionlint, repository-specific checks or Mergify admission.

Major updates are intentionally excluded from Renovate routine automerge by the current root `renovate.json`. Review them as compatibility changes: identify breaking API/configuration/runtime changes, require any necessary migration and tests, and keep them out of the routine autonomous merge path until those changes are evidenced.

Flag duplicate/stale Renovate PRs, unexplained lockfile churn, dependency downgrades, package-manager drift, disabled security checks, or changes that merely silence a failing dependency test.

