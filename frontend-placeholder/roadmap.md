# HIVE Roadmap

## Current build

`v1.14-execution-review-queue`

## Completed stages

- v1.0-v1.8: persistence, R2, D1, chunks, Vectorize, workflow presets, R2 lane registry and skill registry import.
- v1.9: intelligent weighted skill search.
- v1.10: skill recommendation engine.
- v1.11: review-gated skill routing/orchestration.
- v1.12: shared ecosystem execution plan layer.

## Current safety boundary

The shared execution layer is deliberately **plan-only**. HIVE does not install, mutate repos, trigger deploys or execute registry skills automatically. Future execution adapters should require explicit approval and risk gates.

## Next likely phase

v1.13 should add explicit low-risk execution adapters only after review, for example:

- read-only report generation
- skill descriptor inspection
- dry-run patch planning
- MAST-triggered health/status reports

## v1.14 Execution Review Queue

Status: implemented.

Adds D1-backed execution review records and approval decisions for plan-only skill routes. This stage prepares the future operator UI by making recommended actions visible, reviewable and auditable without granting automatic execution.

Next likely stage: explicit low-risk adapter prototypes behind allowlists and dry-run-first gates.
