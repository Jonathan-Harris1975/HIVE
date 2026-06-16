# HIVE production operations

**Status:** Production-controlled  
**Last reviewed:** 16 June 2026

HIVE runs on Koyeb from the root Dockerfile. Use `/livez` for liveness and `/readyz` for readiness. HIVE-UI reaches the API through the Cloudflare Pages proxy; direct browser access is not the normal operator path.

Before deployment, run backend tests, Ruff, dependency checks and the production preflight. After deployment, verify runtime readiness, model discovery, database diagnostics, R2 lanes, a small file-chat dry run and the ecosystem health endpoint.

The `hive` bucket is write-enabled. All additional ecosystem buckets use read-only credentials. A failed read-only lane must degrade that lane without granting broader access or exposing storage credentials.
