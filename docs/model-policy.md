> **Document status:** Production reference  
> **Last reviewed:** 10 August 2026  
> **Operational authority:** Runtime Model Registry + AI Council; environment model IDs are standalone fallbacks only.

# HIVE model policy

HIVE uses OpenRouter as the primary router and keeps provider adapters extensible for OpenRouter-compatible alternatives. Model choice is evidence-led: the persisted Model Registry is authoritative when a qualified model exists; environment defaults keep HIVE independently operational when the registry or benchmark feed is unavailable.

## Selection order

1. An explicit model requested by the operator wins.
2. Otherwise HIVE uses the highest-ranked Model Registry entry for the task category, but only when it clears `MODEL_REGISTRY_MIN_VISIBLE_SCORE`.
3. If no qualified registry entry exists, HIVE falls back to the repository-local environment default for that task.
4. Provider/model preflight and the normal fallback ladder still apply.

Task routing maps to registry categories as follows:

| Task | Registry category | Standalone fallback |
|---|---|---|
| Code | `coding` | `CODE_MODEL` |
| General | `reasoning` | `DEFAULT_MODEL` |
| Audit / brand | `reasoning` | `AUDIT_MODEL` |
| Premium | `reasoning` | `PREMIUM_MODEL` |
| File triage | `long_context` | `BALANCED_MODEL` |
| Summary | `cheap` | `CHEAP_MODEL` |

## Monthly evidence review

MAST triggers `POST /v1/ai-council/run` on the first day of each month before the consolidated HIVE monthly review. The AI Council:

- refreshes configured provider catalogues;
- retrieves OpenRouter's authenticated `/benchmarks` feed when available;
- uses measured Artificial Analysis coding, intelligence and agentic indices as quality evidence;
- combines quality with context, structured-output support and cost through the configurable Benchmark Engine;
- refuses automatic promotion when benchmark evidence does not meet `AI_COUNCIL_AUTO_PROMOTION_MIN_CONFIDENCE`;
- persists qualified registrations to D1 so promotions survive HIVE restarts;
- records each run and promotion in the operations/history lanes.

Provider catalogue metadata by itself is not sufficient for automatic promotion. If measured benchmark retrieval fails, discovery can continue, but catalogue-only candidates remain below the confidence gate.

## Quality floor

```env
AI_COUNCIL_PROMOTION_THRESHOLD=0.72
AI_COUNCIL_AUTO_PROMOTION_MIN_CONFIDENCE=0.60
MODEL_REGISTRY_MIN_VISIBLE_SCORE=0.72
```

Lower-scored models may remain persisted for audit/history, but they are hidden from the normal Models view and are not eligible for automatic task routing. This keeps the operational UI focused on strong candidates rather than presenting the entire provider catalogue as if every model were equally suitable.

## Standalone fallbacks

Environment model IDs are deliberately retained so HIVE can operate without MAST, D1 or a successful monthly review. They are fallbacks, not permanent rankings. `CODE_MODEL` should favour the strongest verified coding model available rather than the cheapest option; the monthly council may replace it at runtime with a better qualified registry model.

## Reliability rules

- OpenRouter model preflight rejects known-dead model IDs before spending the request timeout.
- Transient provider/model failures can use the configured fallback ladder.
- A failed benchmark or catalogue refresh must never prevent HIVE startup or normal use.
- Automatic promotion is reversible because the previous ranked state remains persisted and auditable.
- Explicit operator model selection always remains available for controlled testing.
