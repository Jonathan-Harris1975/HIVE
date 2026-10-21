# Security findings and deployment blockers

[Open this workflow run](https://github.com/Jonathan-Harris1975/HIVE/actions/runs/39540518833)

## Check outcomes

| Check | Outcome | Meaning |
|---|---|---|
| Gitleaks | success | Passed |
| Trivy | failure | Blocks this check; details below or in the named step |
| Workflow lint | success | Passed |
| Diagnostic regression tests | success | Passed |
| Scanner installation | success | Passed |

## Gitleaks

Potential findings need review; a detector match alone does not establish a genuine credential.

**0 potential secret occurrences.** Secret and matching source text are omitted.

| Rule | File at detected commit | Lines | Commit | Fingerprint |
|---|---|---|---|---|

### How to decide

- Open the linked file **at the detected commit**. Full-history scans can find material already removed from the current branch.
- Check whether it is a credential, a documented example, a synthetic fixture or a detector false positive. The report never includes the secret itself.
- If genuine, revoke/rotate it with its provider and remove it from current source. Removal alone does not invalidate a historical credential.
- For a confirmed false positive, record the rule, path, commit/fingerprint and reason in a reviewed change. Do not disable the detector or broadly ignore a directory.

## Trivy

**1 vulnerability/misconfiguration findings.**

| Type / ID | Severity | Target | Package / lines | Installed | Fix / resolution |
|---|---|---|---|---|---|
| Vulnerability / GHSA-6q48-6c7w-9gw2 | CRITICAL | requirements.txt | certifi | 2026.7.22 | 2026.10.19 |

## Other failures

Workflow lint and repository-policy errors remain in their named step logs with file/line details. A missing report is never presented as a clean scan.
The separate **Deployment failure diagnostics** workflow lists failed/cancelled jobs and steps for completed workflow runs after it is installed on the default branch.

Download **security-diagnostics** from this run’s Artifacts section for the Markdown report and metadata-only JSON.
