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

## Rule 3: retry bad endpoints before failing

OpenRouter 404/429/502/503/504 model failures trigger fallback attempts before the request fails.

The final fallback is:

```env
OPENROUTER_FREE_FALLBACK_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
```

## Recommended v1 defaults

```env
DEFAULT_MODEL=~openai/gpt-mini-latest
CHEAP_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
BALANCED_MODEL=~google/gemini-flash-latest
PREMIUM_MODEL=~anthropic/claude-sonnet-latest
CODE_MODEL=x-ai/grok-build-0.1
AUDIT_MODEL=~anthropic/claude-sonnet-latest
OPENROUTER_FREE_FALLBACK_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
```

## AIMS alignment

If AIMS has proven production models, set those same model IDs in Koyeb env vars for the matching lanes. HIVE should still keep extra OpenRouter options available for cheap/general work and testing.

That gives the best blend:

- AIMS-compatible models for brand/audit-sensitive work
- cheaper/free models for smoke tests and low-risk summaries
- premium models only when the task deserves the bill
