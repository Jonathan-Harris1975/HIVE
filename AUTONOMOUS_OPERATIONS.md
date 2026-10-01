# Autonomous operations

## Routine path

Routine low-risk automation is PR-only. Repository CI produces the stable `ci-gate` check. The trusted default-branch admission workflow independently verifies the exact PR head against **CI**, **CodeQL**, and **Security and repository quality** before adding `autonomy:admitted`. Mergify is then the merge authority: it requires `ci-gate`, a conflict-free non-draft PR, resolved review threads, and no hold/manual-review labels before squash-merging and deleting the head branch.

The repository's deployment model is preserved. Post-merge verification uses Koyeb deployment watch and production smoke/health verification. Deployment success and application-health success remain distinct signals.

## Human gates

Human intervention remains required for major dependency updates, credentials/billing/provider changes, destructive migrations, architecture-level changes, ambiguous security findings, or an automated recovery that has exhausted its bounded attempts. Apply `do-not-merge`, `needs-manual-review`, or `autonomy:human-hold` to stop routine admission.

## Recovery and loop prevention

Automated repair is bounded and PR-based. Carrier PRs are lifecycle evidence and are not merged merely because a marker disappears. Kilo implementation PRs must retain a verified source relationship. Governance/security automation changes are placed on human hold. Mergify branch refresh is restricted to admitted Kilo repair branches. Persistent failures escalate through the repository's issue/repair workflow rather than recursively creating unbounded repair attempts.

## Evidence

Use GitHub Actions for exact-SHA CI, CodeQL, security, deployment and repair evidence; the pull-request timeline for admission/merge evidence; Mergify logs for merge-rule evaluation; and repository issues for durable failure escalation. A Council handoff must cite the exact default-branch SHA and must not mark provider-side or destructive validation as passed unless it was actually executed.
