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
