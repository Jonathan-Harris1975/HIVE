# Frontend Placeholder

The backend is Python-first. The UI should be thin and owned by this repo.

Recommended v1 UI:

- React + Vite
- Chat panel
- SSE stream consumer
- Mode selector: Auto / Brand / General / Code / File / Audit
- Model picker populated from `/v1/models`
- Upload panel posting to `/v1/files/upload`
- Token/cost panel once backend persistence lands


## v1.4 backend fields the UI should display

Future UI work should show:

- Build stage from `/health`.
- Storage flags from `/health.storage_flags`.
- Retrieval metadata from file-chat responses: `retrieval_source`, `vector_hits`, `sql_fallback_hits`, and `fallback_used`.
- Cost summary from `/v1/db/cost-summary`.
- Vectorize diagnostics from `/v1/vectorize/diagnostics`.

## v1.12 UI direction

The future operator UI should treat HIVE as a plan-first private ops console:

- Skill search results from `/v1/skills/search` should show score, matched terms and risk level.
- Recommendations from `/v1/skills/recommend` should show why a skill was selected before any route is accepted.
- Routes from `/v1/skills/route` and plans from `/v1/ecosystem/execution-plan` are review-gated and must be presented as dry-run plans, not live execution.
- The UI should clearly label `can_execute_now:false` and approval gates until v1.13+ adds explicit execution adapters.


## v1.13 UI Note

The future HIVE UI can expose repo hygiene as a read-only diagnostics panel showing duplicate groups, orphan candidates and the dry-run deletion manifest. No delete button should be added until a later explicit approval-gated workflow exists.
