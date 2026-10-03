# Branch PR automation

Eligible `fix/`, `feat/`, `chore/`, `ci/`, `work/` and `codex/` branch pushes create or reuse one pull request to the current default branch. Default branches, tags, temporary/internal branches and dependency/repair namespaces with their own PR mechanisms are excluded. Existing human PRs are reused without adopting merge ownership.

The existing default-branch trusted workflow obtains the already-installed Autonomous Repair Bot token. The read-only push signal never executes source code with that token. Hourly and manual recovery handle missed signals; recovery suppresses only an unmerged closure of the same branch tip. Deleted forks and transient CLI merge failures do not interrupt unrelated reconciliation.

Required workflows: **CI, CodeQL, Security and repository quality**.

Required enforced checks: **ci-gate, CodeQL security alert gate, Trivy, Gitleaks and actionlint, Hadolint and ShellCheck, Autonomous repair marker gate, Analyze (actions), Analyze (python)**. All required checks must have strict protection; branch-level merge methods and GitHub review requirements remain enforced. Existing advisory link scans retain their current behaviour. Their removal from the required-status list is a proposed settings change awaiting application.

The controller revalidates the current head and base, then uses `gh pr merge --auto --match-head-commit` with an allowed merge method. GitHub queues native auto-merge or completes an already-permitted merge. There is no admin or unchecked fallback. Drafts, hold/manual-review/superseded/obsolete markers, removal of the management label, and non-green required workflows revoke previously armed native requests. The repository's existing automatic branch-deletion setting handles eligible sources after merge.

`.github/CODEOWNERS` proposes owner review for automation/security configuration. This protection becomes enforced only after `require_code_owner_review` is enabled in the active native ruleset. Ordinary changes outside those paths retain zero routine human approvals. `.github/branch-pr-ruleset.json` contains the proposed final settings; committing it does not apply those settings. Preserve additional existing protections and keep bypass actors empty.

Run the 35 behavioural checks locally and in the security workflow:

```bash
python3 .github/scripts/test_branch_pr_automation.py
```

Deployment remains HOLD until the final settings are applied, installation CI permits merging, and a harmless branch push is verified end to end: exactly one PR, normal CI, duplicate-safe rerun, protected merge and source-branch deletion.
