# Reference Repos

These repos are research references only. No direct dependency or fork relationship is intended.

## ChatLima

Study:

- OpenRouter model list retrieval.
- Model filtering/grouping.
- Model switcher state.
- Server-side API key handling.
- Provider health checks.
- SSE streaming behaviour.

## Kanari

Study:

- Gateway routing architecture.
- Concurrent request handling.
- SSE keep-alive/client disconnect patterns.
- Auth middleware.
- File/context handling.

## OrChat

Study:

- Python-native OpenRouter usage.
- Conversation history management.
- Token tracking.
- Cost estimation.
- Context trimming/summarisation.
- File attachment flow.

## Rule

Borrow ideas, not code baggage.


## v1.4 note

HIVE has now moved beyond reference-study mode into its own operational architecture: FastAPI on Koyeb, R2 for file bodies, PostgreSQL for durable operational state, D1 for ecosystem metadata, and Vectorize/Workers AI for optional semantic retrieval. Reference repos remain inspiration only.

## v1.6 ecosystem lanes

HIVE now recognises AIMS/RAMS/website/podcast artefact lanes through R2 bucket and public-base-url envs. This lets HIVE understand where audit reports, podcast transcripts, RSS feeds, blog artefacts, brand assets and the shared `hive-skills` bucket live.

In this build, lane support is registry/public-URL aware only. Direct multi-bucket ingestion should be added later behind explicit allowlists and tests, once the first HIVE workflows over extracted audit/report bundles are stable.
