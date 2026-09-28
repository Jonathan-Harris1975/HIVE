# Kilo Code Review Guidance

Review pull requests for correctness, regressions, dependency/build consistency, security, tests and deployment safety.

For small dependency or code changes, prioritise concrete blockers over stylistic preferences. `package.json`, lockfiles, Python requirement inputs/locks and deployment configuration may be changed when required by the implementation.

For PRs labelled `autonomy:repair`, the file under `.autonomy/repair-requests/` is a deliberate unresolved marker. The repair is not complete while that marker remains. Verify that the underlying failure is genuinely fixed before accepting removal of the marker.

Never recommend weakening CodeQL, Trivy, Gitleaks, tests, required checks, branch/ruleset protections, workflow permissions, secret handling or security policy merely to obtain a green result. Do not recommend broad suppressions or allowlists for unexplained findings.

The target repository's CI/security/deployment checks are authoritative regardless of whether the change originated from Kilo, Renovate, RAMS, another bot or a human.
