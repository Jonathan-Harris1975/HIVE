# Automatic PR blocker recovery

The existing Autonomous PR issue repair workflow now reconciles conflicts and unresolved review threads after PR workflow completions, review events, pushes to main, manual dispatch and a staggered 20-minute recovery schedule. It runs trusted default-branch code and sends metadata to the configured Kilo repair agent; it never checks out or executes PR code with its reporting token.

Conflicts are routed even when all CI checks passed. The agent is instructed to merge the current base into the existing source branch, preserve both changes, validate, and push without rewriting history. SHA checks and per-head/per-base deduplication bound repeat attempts. Owner holds, drafts, forks, other target branches and repair-carrier PRs are excluded.

Bot review threads require a trusted repair-agent receipt describing the implemented fix and validation, tied to the exact current head and base. The recovery workflow checks all effective required status checks and refreshes both tips before resolving a thread. Human threads are retained. An outdated marker, green CI or a proposed settings file alone is insufficient evidence. Live governance or credential blockers remain explicit. No merge, deployment or protection bypass is added.

The workflow summary and downloadable `pr-blocker-recovery` JSON distinguish routed blockers, pending mergeability, excluded PRs and recovery errors. A failed webhook or missing permission fails recovery visibly without changing source CI results. Two repair dispatches per blocker kind and base are allowed; the exact head is deduplicated.

The agent receipt comment is one line:

```text
<!-- pr-blocker-resolution:{"sha":"CURRENT_HEAD_SHA","base_sha":"CURRENT_BASE_SHA","threads":[{"id":"PRRT_ID","evidence":"Implemented fix location and concrete validation evidence"}]} -->
```

Only the configured Kilo implementation identity or installed repair App identity is trusted to supply receipts. The workflow uses pull-request write permission solely to resolve verified bot review threads. Existing native merge protections and reviewer decisions remain authoritative.
