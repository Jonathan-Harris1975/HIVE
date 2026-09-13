> **Document status:** Production control plan  
> **Last reviewed:** 12 September 2026  
> **Control owner:** HIVE AI Council  
> **Scope:** HIVE, AIMS and RAMS model selection through OpenRouter

# LLM model selection governance plan

1. **Make the HIVE AI Council accountable for the model catalogue.**

   - Run the formal Council cycle monthly, before the consolidated monthly review.
   - Trigger an out-of-cycle review within one working day when OpenRouter announces a retirement, changes a configured alias, materially changes price/provider coverage, or internal quality deteriorates.
   - Keep one named service owner and one Council approver for each production route: `cheap`, `fast`, `reasoning`, `coding`, `long_context`, `audit` and `premium`.
   - Treat the D1 Model Registry as the runtime authority. Environment model IDs are last-known-good standalone fallbacks, not permanent rankings.

2. **Check OpenRouter before every approval or renewal.**

   - Fetch `/api/v1/models` and record `id`, `canonical_slug`, `expiration_date`, input/output price, context, modalities, supported parameters and provider availability.
   - Fetch measured benchmark evidence and combine it with HIVE task evals. A catalogue entry alone is not enough for promotion.
   - Re-check the intended model page immediately before a material rollout; the table below is a dated shortlist, not a permanent allow-list.

   | Route | Current OpenRouter candidate at review date | Practical disposition |
   |---|---|---|
   | General, summary and most file work | `~google/gemini-flash-latest` (then resolving to Gemini 3.8 Flash; $0.75/M input, $3.75/M output, 1.048M context) | Keep as the baseline while HIVE evals meet the quality floor; govern the resolved canonical slug. |
   | Complex reasoning / premium | `~anthropic/claude-sonnet-latest` (then resolving to Sonnet 5; $2/M input, $10/M output, 1M context) | Approved premium candidate only; operator override needs justification. |
   | Coding | `openai/gpt-5.3-codex` ($1.75/M input, $14/M output, 400K context) | Keep only where coding eval uplift offsets its higher output cost. |
   | Low-cost challenger | `z-ai/glm-5.3-flash` ($0.075/M input, $0.25/M output, 1.31M context) | Shadow-test against the baseline; do not promote from public benchmarks alone. |
   | Free fallback | `nvidia/nemotron-3-ultra-550b-a55b:free` | Public, non-sensitive, best-effort work only; never the first production fallback. |

   At this review, the configured routes were present and did not publish a retirement date. That is not a guarantee: moving `~...-latest` aliases can change their target. Council records must therefore use `canonical_slug`; aliases are for discovery and emergency continuity only.

3. **Classify the work before selecting a model.**

   - Data: `public`, `internal`, `confidential` or `restricted`. Default to `internal` when omitted.
   - Complexity: Tier 0 deterministic/no LLM; Tier 1 extraction, classification or summary; Tier 2 normal generation, grounded analysis or tool use; Tier 3 difficult coding, multi-step reasoning or high-impact review.
   - Output contract: plain text, JSON/schema, tools, vision/audio and minimum context.
   - Risk: reversible/low-impact, externally published, security/compliance, or production-changing.
   - Do not send internal, confidential or restricted content to a free endpoint. This is a data control, not a cost control.

4. **Select the least expensive model that passes the task-specific acceptance bar.**

   | Task class | Default route | Required evidence | Escalate only when |
   |---|---|---|---|
   | Tier 0 | No model | Deterministic test coverage | An LLM is demonstrably necessary. |
   | Tier 1 | `cheap` / `fast` | Accuracy and structured-output pass rate | Baseline fails the agreed acceptance threshold. |
   | Tier 2 | `reasoning` / `long_context` | Grounded quality, reliability, latency and cost per successful task | Cheaper qualifying routes fail representative cases. |
   | Tier 3 | `coding`, `audit`, or approved `premium` | Domain evals, safety review and measured uplift over baseline | The expected quality/risk reduction exceeds incremental cost. |

   Compare candidates on cost per **successful** task, not headline token price. Use an 80:20 input/output token mix for initial catalogue estimates, then replace it with the route's observed mix.

5. **Use category-specific scoring and minimum gates.**

   - Apply hard capability gates first: required modality, context, tools, structured output, data policy, lifecycle state and minimum quality.
   - Rank only models that pass those gates. Coding weights coding quality/reliability most; cheap/fast weights cost/latency more; long-context weights usable context/reliability more.
   - Use `AI_COUNCIL_PROMOTION_THRESHOLD` and `AI_COUNCIL_AUTO_PROMOTION_MIN_CONFIDENCE` for automatic promotion. Missing evidence stays visible for review but is not silently treated as proof.
   - Run a fixed golden set plus recent production samples. Record success rate, p50/p95 latency, input/output tokens, actual OpenRouter cost, fallback rate, schema/tool success and human rework.
   - Require a statistically and operationally meaningful uplift before replacing a healthy baseline. Use shadow or canary traffic first and retain a rollback model.

6. **Use a controlled routing and fallback order.**

   - Normal order: qualified Council registry model → stable paid baseline(s) → free endpoint only for explicitly public data.
   - Keep premium models out of fallback ladders unless the current request has valid premium approval.
   - Try enough stable candidates to make the fallback configuration real (`OPENROUTER_MAX_FALLBACK_ATTEMPTS=2` by default).
   - Provider/model preflight skips known-dead explicit IDs. A catalogue or benchmark outage must not stop normal work; use the last-known-good registry/environment route.
   - Never set a provider maximum-price filter or hard workspace budget. Cost thresholds generate alerts and review tasks only; they do not block workloads.

7. **Require recorded justification for an expert or more expensive override.**

   A request for Claude Opus, GPT-4-class models, the configured premium route, GPT-5.3 Codex outside its governed coding default, or another pattern in `MODEL_GOVERNANCE_PREMIUM_PATTERNS` must include:

   - approval ID and reason code;
   - cheaper model considered and why it was insufficient;
   - eval, failed example or other evidence;
   - expected calls, input tokens, output tokens and estimated total cost;
   - scope (`single_request`, `workflow`, `repository` or `standing`), approver and expiry;
   - rollback model; and
   - actual quality/cost outcome when the work closes.

   Standing approvals expire in no more than 90 days. Missing, incomplete or expired evidence causes HIVE to use the governed baseline and records the reason; it does **not** stop the workload. An emergency override may proceed only with the same recorded fields, `emergency=true`, a named approver and review at the next working-day checkpoint.

8. **Manage retirement as a lifecycle, not a surprise outage.**

   - `active`: more than 60 days to expiry, or no published expiry.
   - `watch`: 31–60 days; identify and evaluate a successor.
   - `deprecating`: 8–30 days; stop new default promotions and migrate callers.
   - `quarantined`: 1–7 days; remove from automatic routing and fallbacks.
   - `retired`: expiry passed or disappeared from the provider catalogue; retain history but never route.
   - The Council records planned expiries from OpenRouter `expiration_date`, detects disappeared IDs against the previous snapshot, updates every matching registry entry, and distributes only `active`/`watch` entries to AIMS and RAMS.

9. **Record every model decision and every billable call.**

   - Persist requested model, governed baseline, selected model, task, selection source, data classification, premium evidence/validation issues and whether a free fallback was allowed.
   - Request OpenRouter usage accounting on chat, file chat, auto-title and repository-improvement calls; write actual tokens and cost to `hive_cost_events`.
   - Retain Council input evidence, weights, canonical model, lifecycle status, promotions and downstream sync outcome.
   - Do not store secrets in justification text. Apply the normal HIVE retention and access controls to prompts, evidence and cost records.

10. **Audit and rationalise existing usage monthly.**

    1. Export 30/60/90-day requests and cost by route, model, team, use case and data class.
    2. Reconcile OpenRouter billing with `hive_cost_events`; investigate missing usage, duplicate retries and unallocated system calls.
    3. Flag premium use without valid approval, approved work with no actual outcome, emergency use, free-model policy substitutions and expired approvals.
    4. Compare each expensive route with the cheapest candidate that passed the same eval; calculate incremental cost, quality uplift and cost per successful task.
    5. Identify low-volume models, overlapping routes, high fallback/error rates, poor cache/reuse, excessive output tokens and aliases whose canonical target changed.
    6. Decide `retain`, `downshift`, `consolidate`, `re-evaluate`, `migrate` or `retire`, with owner and due date.
    7. Canary changes, verify quality and cost, then update the registry. Preserve the previous route for rollback.
    8. Publish the decision log and unresolved actions in the HIVE monthly review. Cost alerts remain advisory.

11. **Require this decision framework before a team chooses a model.**

    ```mermaid
    flowchart TD
        A["Classify task and data"] --> B{"LLM needed?"}
        B -- No --> C["Use deterministic code"]
        B -- Yes --> D["Apply capability and lifecycle gates"]
        D --> E["Test cheapest qualified route"]
        E --> F{"Acceptance bar met?"}
        F -- Yes --> G["Use baseline and record usage"]
        F -- No --> H{"Premium evidence complete?"}
        H -- Yes --> I["Use approved premium route"]
        H -- No --> G
    ```

    Teams must answer **yes** to every applicable item before launch:

    - [ ] Task, data classification, risk and output contract are recorded.
    - [ ] Deterministic/non-LLM handling was considered.
    - [ ] OpenRouter availability, canonical slug, price and retirement date were refreshed.
    - [ ] The cheapest capable candidate was tested on representative HIVE evals.
    - [ ] Quality, reliability, latency and actual cost per successful task meet the route's bar.
    - [ ] Free endpoints are excluded unless the data is explicitly public.
    - [ ] Any premium override has complete, approved, expiring evidence and a rollback model.
    - [ ] Usage/cost telemetry, owner, canary and review date are configured.

12. **Operate approval and exception workflows with clear service levels.**

    - Requestor supplies the completed evidence record and test results.
    - Route owner validates technical fit and expected volume; Finance/operations sees advisory cost impact.
    - Council approver accepts, rejects or time-bounds the override. Security/privacy joins for confidential or restricted data.
    - HIVE records the decision in chat/file metadata and the monthly governance audit. AIMS and RAMS receive only eligible registry routes.
    - Normal review target: two working days. Emergency review: next working day. Retirement inside seven days: immediate out-of-cycle Council review.

13. **Measure whether governance is working.**

    Track monthly: percentage of calls on baseline/cheap routes; premium request and approval rates; premium cost and measured uplift; cost per successful task; fallback/retry rate; free-policy substitutions; lifecycle migration lead time; unallocated cost; and approvals missing actual outcomes. Alert owners on unexpected variance, but never block requests solely because a budget or cost threshold was crossed.

14. **Roll out in four controlled stages.**

    1. **Observe (week 1):** deploy decision/cost telemetry and lifecycle ingestion; do not change defaults.
    2. **Shadow (weeks 2–3):** compare the baseline with `z-ai/glm-5.3-flash` and other qualified challengers on HIVE evals.
    3. **Enforce governance (week 4):** require premium evidence, make free fallback public-only and exclude deprecating/retired routes. Invalid overrides continue on baseline.
    4. **Rationalise (monthly):** consolidate or downshift routes based on measured outcomes, canary changes and close approval records.

15. **Use the implemented request contract.**

    Chat and file-chat requests accept `data_classification` and `model_justification`. The justification object uses the fields in step 7. Responses and SQL message metadata return `model_governance`, including whether an override was approved or replaced. Relevant environment controls are:

    ```env
    MODEL_GOVERNANCE_ENABLED=true
    MODEL_GOVERNANCE_PREMIUM_PATTERNS=claude-opus,gpt-4,gpt-5.3-codex
    MODEL_GOVERNANCE_APPROVAL_MAX_DAYS=90
    OPENROUTER_FREE_FALLBACK_PUBLIC_ONLY=true
    MODEL_RETIREMENT_WATCH_DAYS=60
    MODEL_RETIREMENT_DEPRECATING_DAYS=30
    MODEL_RETIREMENT_QUARANTINE_DAYS=7
    OPENROUTER_MAX_FALLBACK_ATTEMPTS=2
    ```
