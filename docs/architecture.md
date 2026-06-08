# Architecture

## Principle

Standalone first. External repos are studied, not inherited.

## Core flow

```text
User request
  -> Auth middleware
  -> Mode classifier: Auto / Brand / General / Code / File / Audit
  -> Task classifier
  -> Model router
  -> Context manager
  -> OpenRouter streaming client
  -> SSE response
```

## File flow

```text
Upload
  -> size check
  -> safe temp file
  -> sha256
  -> R2 or local fallback storage
  -> type detection
  -> ZIP inspection if needed
  -> text extraction if supported
  -> chunking
  -> metadata/indexing later
```

## Modes

- Auto: infer suitable mode.
- Brand: Jonathan Harris ecosystem tone and context.
- General: neutral assistant mode.
- Code: strict technical/code review mode.
- File Analysis: source-grounded file mode.
- Audit: production-readiness and QA mode for AIMS/RAMS/workflows.

## Model routing

Initial tiers:

- Cheap: summaries and quick triage.
- Balanced: normal file analysis and general work.
- Premium: critical reasoning.
- Code: repo/code/debugging.
- Audit: production, quarantine, CI, RAMS/AIMS analysis.

## SSE streaming

The backend uses a single OpenRouter streaming wrapper and normalises events to:

- `token`
- `keepalive`
- `error`
- `done`

## Source strategy

The app should answer from extracted/indexed chunks, not whole raw files, to reduce cost and improve traceability.


## Project glossary

- HIVE = the private OpenRouter-powered ops chatbot/console in this repo.
- AIMS = the user's AI/content automation and management ecosystem.
- RAMS = the user's reporting, audit, monitoring, and production-readiness system for AI/content workflows.
- Do not use the construction/legal “Risk Assessment Method Statement” meaning of RAMS unless explicitly requested.
