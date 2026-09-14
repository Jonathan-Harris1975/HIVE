# HIVE context resilience

HIVE uses workload-specific context routing. General work defaults to **Context Gateway**; coding work defaults to **LeanCTX**. Both are external OpenAI-compatible proxies, not hard runtime dependencies.

General default:

`Context Gateway -> Headroom -> OpenRouter context-compression -> deterministic local compaction -> direct OpenRouter`

Coding default:

`LeanCTX -> Context Gateway -> Headroom -> OpenRouter context-compression -> deterministic local compaction -> direct OpenRouter`

## Primary selection

General primary: `HIVE_CONTEXT_PRIMARY_PROVIDER`

Coding primary: `HIVE_CONTEXT_CODING_PRIMARY_PROVIDER`

Accepted values are `leanctx`, `context_gateway`, `headroom`, `openrouter`, `deterministic`, and `direct`.

Fallback lists are independently configurable with `HIVE_CONTEXT_FALLBACK_PROVIDERS` and `HIVE_CONTEXT_CODING_FALLBACK_PROVIDERS`. The direct OpenRouter path is always retained as the final break-glass route.

## External proxies

Set `HIVE_LEANCTX_BASE_URL` and `HIVE_CONTEXT_GATEWAY_BASE_URL` to the deployed OpenAI-compatible API roots, normally including `/v1`. Blank URLs are skipped with no network delay. If client authentication is required, configure `HIVE_LEANCTX_API_KEY` / `HIVE_CONTEXT_GATEWAY_API_KEY` via deployment secrets.

The proxy service is responsible for its own OpenRouter upstream credential. HIVE strips `_hive_context_profile` before sending any request outside the application.

File/AnchorPatch requests retain the existing exact-context protection: Headroom becomes a pass-through and the lossy OpenRouter/local compaction routes are skipped.
