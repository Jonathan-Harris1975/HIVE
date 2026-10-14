# Autonomous operations

## Routine path

Routine low-risk automation is PR-only. Repository CI produces the stable `ci-gate` check. The trusted default-branch admission workflow independently verifies the exact PR head against **CI**, **CodeQL**, and **Security and repository quality** before adding `autonomy:admitted`. GitHub native auto-merge is then enabled only after `ci-gate`, all other required checks, conflict-free non-draft state, resolved review threads and lifecycle-label policy pass. GitHub applies active rulesets before merging.

The repository's deployment model is preserved. Post-merge verification uses Koyeb deployment watch and production smoke/health verification. Deployment success and application-health success remain distinct signals.

## Human gates

Human intervention remains required for major dependency updates, credentials/billing/provider changes, destructive migrations, architecture-level changes, ambiguous security findings, or an automated recovery that has exhausted its bounded attempts. Apply `do-not-merge`, `needs-manual-review`, or `autonomy:human-hold` to stop routine admission.

## Recovery and loop prevention

Automated repair is bounded and PR-based. Carrier PRs are lifecycle evidence and are not merged merely because a marker disappears. Kilo implementation PRs must retain a verified source relationship. Governance/security automation changes are placed on human hold. The weekly settle stage nudges trusted automation and waits for native auto-merge to close eligible admitted PRs. Persistent failures escalate through the repository's issue/repair workflow rather than recursively creating unbounded repair attempts.

## Evidence

Use GitHub Actions for exact-SHA CI, CodeQL, security, deployment and repair evidence; the pull-request timeline for admission/merge evidence; GitHub native auto-merge status and the pull-request timeline for merge-policy evidence; and repository issues for durable failure escalation. A Council handoff must cite the exact default-branch SHA and must not mark provider-side or destructive validation as passed unless it was actually executed.
