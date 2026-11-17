# Autonomous agent policy: repository forking

This policy applies to all agents and automation acting on this repository, including ChatGPT/Work, Kilo, HIVE, RAMS, MAST, GitHub Actions, repair bots, councils and any delegated sub-agent.

## Default: no forks without express owner approval

- NEVER create a GitHub fork of this repository or of another repository on the owner's behalf without an explicit, case-specific approval from the repository owner, Jonathan-Harris1975.
- This prohibition covers GitHub web/API/CLI/SDK fork operations (including gh repo fork, hub fork and POST /repos/{owner}/{repo}/forks), creating a remote mirror or copy to bypass the restriction, asking another actor to do so, and retrying with another identity or token.
- A broad instruction to repair, merge, maintain, work autonomously, or do whatever is necessary is NOT approval to fork. No default, standing or implied consent. Do not infer approval from access to credentials or ability to call an API.
- If a fork is genuinely indispensable, stop only the fork-dependent action and set HUMAN_HOLD. Record the source repository, intended destination account/repository, reason, actor and requested scope. Wait for the owner's affirmative approval for that exact operation. No automatic approval, expiry extension or fallback to another account.
- All ordinary development, autonomous repair, dependency updates and branch-to-PR automation MUST use a branch of the original repository and a same-repository pull request. Existing required CI, security checks, exact-head validation, branch/ruleset protection and merge authority remain mandatory.
- Incoming PRs from external forks are untrusted. Do not auto-admit or auto-merge them; do not expose write tokens/secrets or execute their source in privileged pull_request_target or workflow_run jobs. Human-controlled review and normal protections are required.
- Do not grant new repository-creation, administration, classic PAT or cross-account GitHub permissions to work around this rule. Do not change this policy, the fork guard, or its required security job autonomously to make a fork possible.

## Operational behaviour

Fork denied or approval missing: record HUMAN_HOLD for that task, continue independent routine self-healing and CI work, and escalate to the owner. Do not block Headroom or otherwise suspend unrelated healthy services.

A local git clone, a branch in the existing repository, and a same-repository pull request are not forks and remain permitted.

This policy regulates this owner's automation. GitHub permits third parties to fork public repositories; this file cannot override that platform behaviour.
