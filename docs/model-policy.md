# HIVE model policy

HIVE keeps model choice configurable through environment variables rather than hard-coding one provider or one dated model ID.

## Rule 1: explicit model wins

If a request includes `model`, the backend sends that exact model ID to OpenRouter first.

```json
{
  "mode": "general",
  "model": "nvidia/nemotron-3-ultra-550b-a55b:free"
}
```

## Rule 2: mode chooses a default only when model is blank

| Task / mode | Env var used |
|---|---|
| General | `DEFAULT_MODEL` |
| Summary | `CHEAP_MODEL` |
| File triage | `BALANCED_MODEL` |
| Code | `CODE_MODEL` |
| Brand / audit | `AUDIT_MODEL` |
| Premium | `PREMIUM_MODEL` |

## Rule 3: preflight dead model IDs before calling OpenRouter

By default, HIVE checks the requested model ID against OpenRouter's current model list before making the chat call. If the requested model is not available, HIVE skips it immediately and tries the fallback ladder instead. This prevents a known-dead model from burning the full HTTP timeout before fallback.

```env
OPENROUTER_MODEL_PREFLIGHT_ENABLED=true
OPENROUTER_MODEL_LIST_TIMEOUT_SECONDS=10
```

If the model-list check itself fails, HIVE fails open and attempts the requested model rather than blocking chat entirely.

## Rule 4: retry bad endpoints before failing

OpenRouter 404/408/429/500/502/503/504 model failures trigger fallback attempts before the request fails. Individual non-streaming attempts are timeout-limited.

By default, fallback is **free-first and free-only**:

```env
OPENROUTER_FREE_FALLBACK_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
ALLOW_PAID_FALLBACK=false
OPENROUTER_ATTEMPT_TIMEOUT_SECONDS=12
OPENROUTER_MAX_FALLBACK_ATTEMPTS=2
OPENROUTER_EMPTY_REPLY_RETRY_ENABLED=true
OPENROUTER_MIN_RESPONSE_TOKENS=80
```

Set `ALLOW_PAID_FALLBACK=true` only when a production lane is allowed to escalate from a dead/overloaded model into paid alternatives. This prevents smoke tests and low-risk calls quietly rolling into paid models.

## Recommended v1 defaults

```env
DEFAULT_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
CHEAP_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
BALANCED_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
PREMIUM_MODEL=~anthropic/claude-sonnet-latest
CODE_MODEL=x-ai/grok-build-0.1
AUDIT_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
OPENROUTER_FREE_FALLBACK_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
ALLOW_PAID_FALLBACK=false
OPENROUTER_MODEL_PREFLIGHT_ENABLED=true
OPENROUTER_MODEL_LIST_TIMEOUT_SECONDS=10
OPENROUTER_ATTEMPT_TIMEOUT_SECONDS=12
OPENROUTER_MAX_FALLBACK_ATTEMPTS=2
OPENROUTER_EMPTY_REPLY_RETRY_ENABLED=true
OPENROUTER_MIN_RESPONSE_TOKENS=80
```

## AIMS alignment

If AIMS has proven production models, set those same model IDs in Koyeb env vars for the matching lanes. HIVE should still keep extra OpenRouter options available for cheap/general work and testing.

That gives the best blend:

- AIMS-compatible models for brand/audit-sensitive work
- cheaper/free models for smoke tests and low-risk summaries
- premium models only when the task deserves the bill

## Brand glossary guardrail

Brand and audit prompts define the user's ecosystem terms explicitly:

- HIVE: this private OpenRouter-powered ops console.
- AIMS: the user's AI/content automation and management ecosystem.
- RAMS: the user's reporting, audit, monitoring, and production-readiness system.

HIVE must not interpret RAMS as “Risk Assessment Method Statement” unless the user explicitly asks for that construction/legal meaning.


## Empty visible replies

Some free/reasoning-heavy models can consume a very small `max_tokens` budget in hidden reasoning and return no visible assistant text. HIVE now treats that as an incomplete model attempt, not a successful response.

Behaviour:

1. If a model returns no visible text, HIVE records an `empty_reply` attempt.
2. HIVE tries the configured fallback ladder.
3. If every attempt is empty or failed, the API returns `ok:false`, `error_code:"empty_model_reply"`, and a readable diagnostic reply.
4. Direct and file-chat endpoints include `completion_truncated` and `empty_reply` fields.

Recommended smoke-test setting:

```env
OPENROUTER_EMPTY_REPLY_RETRY_ENABLED=true
OPENROUTER_MIN_RESPONSE_TOKENS=80
```

This keeps phone/ReqBin tests from accidentally asking a reasoning model to answer with too little output room.


## File-chat timeout diagnostics

`/v1/chat/with-file` supports `dry_run:true` / `skip_model:true` to verify R2 read and prompt construction without a model call. Configure the model-call guard with:

```env
CHAT_WITH_FILE_MODEL_TIMEOUT_SECONDS=30
```

The endpoint returns `stage`, `timings`, and `error_code:"chat_with_file_timeout"` instead of a hanging request when model calls exceed the guard.


## v1.4 retrieval policy

For file answers, HIVE should prefer chunked retrieval over raw prompt stuffing. Vectorize is allowed to improve semantic matching, but SQL chunks remain the source of truth. If Vectorize or embeddings fail, HIVE should fall back to SQL chunk search rather than blocking file chat.

Responses should expose retrieval metadata so operators can tell whether an answer used Vectorize or SQL fallback.

## v1.6 preset policy

Workflow presets do not bypass model routing. They tune the mode, retrieval defaults and output framing before the normal model router chooses or validates the model.

Default stance:

- Audit/content/podcast/eBook presets use Brand/Audit framing where appropriate.
- Repo/debug and CI/log presets use Code framing.
- SQL fallback remains enabled for all presets.
- `ci_log_analysis` prefers SQL retrieval first because exact log terms are usually more useful and cheaper than semantic search.
- Presets are free-tier safe by default and avoid expanding chunk limits into heavy background work.

The model router still respects explicit `model` when supplied by the caller.
