# Daily LLM spend in HIVE-UI

Operations → LLM spend (`/llm-spend`) reads provider-recorded OpenRouter evidence through the existing authenticated HIVE gateway. HIVE exposes read-only `GET /v1/system/llm-spend`, with an optional completed `date=YYYY-MM-DD`. The endpoint inherits `require_admin` and returns `Cache-Control: no-store`.

The server reads only exact daily snapshot keys in `R2_BUCKET_META_SYSTEM`, using existing read-only R2 credentials. It never invokes an LLM, requests a provider refresh, lists arbitrary objects or returns provider credentials, application identifiers or API-key dimensions. AIMS remains responsible for collection. The read credentials must include the private metasystem reporting objects.

The page shows dated USD usage charges, a sourced GBP estimate when present, request and supported token counts, a reconciled model breakdown, collection freshness and previous-day/same-weekday comparisons where snapshots exist. Missing evidence stays unavailable; verified zero usage remains zero. Comparisons describe account spend changes, not efficiency gains. Provider totals are provisional.

GBP is deliberately unavailable when the snapshot has no dated, sourced conversion. Actual card settlement, task/repository allocation, podcast separation, retries, cost per successful standard and quality outcomes are not inferred. These require a verified join with operational evidence. The historical £6-per-standard reference is not a measured baseline.

The whole account is included, since OpenRouter is the only current LLM provider. Reading the page or pressing Reload report only reads stored evidence; it does not run a council, retry an LLM task, or refresh provider billing.
