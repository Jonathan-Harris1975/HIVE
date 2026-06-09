# Roadmap

## v0.1 Scaffold

- [x] FastAPI app structure
- [x] OpenRouter model list endpoint
- [x] OpenRouter streaming client
- [x] SSE normalisation
- [x] Bearer token auth
- [x] R2 storage adapter
- [x] Local blob fallback
- [x] ZIP safety inspection
- [x] Text extraction skeleton
- [x] Model router
- [x] Brand / General / Code / Audit modes
- [x] Starter tests

## v0.2 Persistence

- [ ] SQLAlchemy models for conversations, messages, files, chunks, costs
- [ ] SQLite local database
- [ ] PostgreSQL production path
- [x] File list/read API
- [x] Single-file chat API
- [ ] Durable file metadata API
- [ ] Conversation resume API

## v0.3 Search

- [ ] Embedding worker
- [ ] Cloudflare Workers AI embeddings bridge
- [ ] Vectorize upsert/query integration
- [ ] Source-cited RAG answers

## v0.4 UI

- [ ] React/Vite chat UI
- [ ] Mode selector
- [ ] Model picker
- [ ] Upload panel
- [x] R2 file browser API
- [ ] R2 file browser UI
- [ ] Cost display

## v0.5 Ops features

- [ ] AIMS/RAMS audit report reader
- [ ] Quarantine report review
- [ ] Koyeb/GitHub log analysis upload mode
- [ ] Social/post QA lanes
