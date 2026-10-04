# Council merge-window privileged-trigger audit

The two `dangerous-triggers` exceptions apply only to the `pull_request_target` and `workflow_run` declarations in `council-merge-window.yml`. They acknowledge intentional privileged metadata reconciliation. No other audit, workflow, job, checkout or package install is excluded.

## Executable boundary

The controller checks out only `github.event.repository.default_branch` with a commit-pinned checkout Action and `persist-credentials: false`. It never checks out the PR head or triggering run head. It runs the protected `.github/scripts/merge_window.py`, importing the protected receipt verifier and `.council` policy. Neither module executes event text, PR files, downloaded artifacts, shell commands or dynamically imported PR code. The workflow passes only runner-owned token and configured repository variables through environment values; event fields are not interpolated into a shell script.

The job grants only contents/actions/pull-request read and statuses write. It cannot write repository contents, approve PRs, merge PRs, alter settings or create repair implementation code. It receives no repair App key or deployment secret. The script reads authenticated GitHub metadata and writes exact-head status contexts; it does not download or execute artifact content.

## Evidence boundary

Trigger names wake the controller; they cannot certify a cycle. It independently discovers canonical workflow IDs, current default SHA, authenticated actor and triggering actor, latest run and attempt, London slot/envelope, conclusion, retained nonexpired artifact digests and open holds. A receipt must come from a completed successful canonical default-branch publisher and the configured Kilo App actor. Repair authorization must come from a completed successful canonical trusted default-branch producer and bind PR head, failed run, freeze run/attempt and current base. Direct statuses, human reruns, missing artifacts, superseded failures and divergent histories do not release a freeze.

The CI recorder, controller and trusted admission share one repository concurrency group. Reconciliation revokes existing open-PR window success before fallible evidence discovery and rechecks the default and PR heads before publication. Failures leave ordinary admission blocked.

## Verification and protection

The 23 receipt and 29 freeze/admission tests cover these authenticated and negative paths, including forged statuses, expired or absent artifacts, wrong workflows/actors/heads, old attempts, superseded failures and revocation before API errors. Full local actionlint and zizmor were rerun after the comments. Other workflow findings remain visible; the scanner remains blocking and the seeded unsafe-workflow acceptance evidence remains separate.

The exception comments, this audit, controller and verifier are governance/security changes within the existing Kilo-sensitive `.github/workflows/` and `.github/scripts/` prefixes. Repair admission places changes there on human hold. Changes require the normal protected review path. These draft exceptions do not establish live freeze acceptance or activate the native required context; both remain HOLD prerequisites.
