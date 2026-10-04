# CI automation ownership

Renovate alone chooses dependency versions, updates manifests/locks and opens dependency PRs. Dependabot configuration is passive: every version PR limit is zero. Disabling Dependabot security-update PRs is a separate live repository setting and must be verified independently.

Renovate never merges, enables native auto-merge or posts Mergify queue commands. Approved existing minor/patch/digest, security and lock-maintenance policies generate `dependency:auto-eligible`. Major/manual rules generate `dependency:manual`, which takes precedence. Trusted admission checks the exact Renovate identity, same repository, `renovate/` namespace, eligibility and exact-head required workflows before admission. Mergify alone merges admitted automation. PRs already armed for native auto-merge are withheld from Mergify admission.

The native ordinary branch lane excludes `renovate/`, `dependabot/`, `autonomy/`, `mergify/`, `automation/` and `codex/`. Codex changes use a dedicated branch and PR with all normal checks. This bootstrap PR has no self-merge authority. Kilo repairs established application behaviour through PRs and cannot change manifests, dependency locks or protected governance controls. autofix.ci remains mechanical only.

Production dependency rollback selects the exact failed merge SHA, merged Renovate PR and main base. It opens a recovery PR and grants no direct merge authority.

## Controls and timing

The existing required security check names and scanner thresholds remain unchanged. actionlint checks workflow correctness; zizmor checks workflow security. Strict Renovate validation runs in the security workflow. Harden-Runner observes the security job in audit mode. Lychee retains the repository-specific existing advisory/blocking policy.

Renovate refresh: `after 9am and before 11am on saturday`, Europe/London. CI: Sat 11:00; DAST: Sun 02:00; Council: Sun 16:00. The launcher dispatches CI, CodeQL and Security as one phase. DAST opt-in and targets are unchanged. Routine merging after final CI PASS must remain frozen until exact-SHA Council certification. A demonstrated freeze/receipt mechanism remains an outstanding acceptance requirement.

## Live verification still required

Dependabot security-update settings, complete installed App scopes, live Dependency Review support, clean scanner baselines, real safe/manual Renovate PR behaviour, exact-head admission/Mergify merge, runner audit evidence, Council freeze and deployment provenance/OIDC applicability must be proved before READY. Source-built deployments or transient CI image tags must not gain artificial attestation uploads. Existing overlapping CI bootstrap PRs must be reconciled before merge so older code cannot restore obsolete admission or capture Codex branches.

Admission now publishes the `Trusted automation admission` status on the exact verified PR head using the default-branch controller’s scoped status token. Mergify requires this proof for admission/queue/merge and vetoes `dependency:manual` at every stage. Reconciliation resets stale proof to pending and revokes the admission label before evaluating current policy. A changed head cannot inherit the successful proof from its predecessor. Renovate is explicitly denied Mergify queue-command access even if its App has write permission.

The launcher authenticates the event’s scheduled UTC cron against the prescribed London start and current DST offset. Delayed starts remain accepted only inside the fixed allocated window; the alternate DST cron is rejected. Independent CI/scanner/DAST phase crons are removed. Targets, opt-in, reporting and existing application operations are preserved.

Dependabot security-update PR creation is OFF in live repository settings; alerts and dependency graphs remain ON. Auto-triage has no custom PR-creation rule. The pinned native Dependency Review job cannot choose versions, alter manifests or create update PRs. Existing scanner and licensing policies are retained.

## Council completion receipt

`council-completion.yml` verifies an authenticated Kilo App completion against live canonical exact-SHA workflows, deployment evidence and existing DAST opt-in before retaining a receipt and publishing `Repository Council acceptance`. The configured Kilo identity uses the existing `KILO_REPAIR_PR_LOGIN`; no new App permissions or external callback endpoint are added. Receipt delivery and persistent freeze enforcement are implemented in this draft and remain unproved live. A successful Council dispatch alone cannot certify or release an envelope.

The read-only `accepted_receipt` consumer revalidates the current default head and live evidence, checks a completed canonical verifier run, checks the retained receipt digest and rejects receipts predating the envelope start. A Kilo-written status alone cannot authenticate acceptance. Twenty-three receipt publisher/consumer tests pass locally. The consumer is connected to the persistent merge-freeze controller in this draft; live native-protection activation and end-to-end acceptance remain outstanding.


## Authenticated Council freeze

The default-branch CI workflow records its evidence marker only after the CI
aggregate succeeds inside this repository's fixed London CI slot. It revokes
previous PR merge-window successes before completing final CI PASS, then retains
an exact run/attempt/SHA freeze artifact. The marker survives repair merges.
The CI recorder, scheduled/event controller and trusted admission job share one
repository concurrency group and never execute PR code with a write token.

`Council merge window` is required by Mergify at protection, queue and merge
boundaries. Native branch protection must require this GitHub Actions context
and Dependency Review once the protected controllers are installed; enabling the
native requirements and a full live freeze/repair/re-certification demonstration
remain release prerequisites. Configuration and unit tests alone are not live
acceptance.

A frozen cycle allows only a Kilo implementation already verified by the
existing trusted admission controller, linked to the authenticated repair App's
carrier for a still-failing canonical default-branch run in the current weekend
envelope. A completed trusted default-branch authorization run must retain an
exact implementation-head/base/failure/freeze-run/attempt artifact. A label,
comment, direct status, stale base or superseded failure cannot grant the
exception. Dependency manifests, locks and protected controls remain outside
Kilo's automated repair authority.

Only completed authenticated Council receipt publication releases the freeze.
Retained receipt digests, canonical source Council and completion runs, Kilo
actor and triggering actor, current exact-SHA evidence and unresolved holds are
checked. A later ordinary merge may advance a completed cycle; divergent
history cannot inherit it. A new CI attempt invalidates an older release. Missing
or expired evidence and bounded-pagination failures stop admission. No workflow
scanner exception is added for this controller; trigger audit findings remain
visible for review.
