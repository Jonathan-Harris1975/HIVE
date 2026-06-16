# Security policy

HIVE is a private administrative backend. Do not expose credentials in issues, logs, screenshots, browser bundles, or commits.

## Operational rules

- Keep `ADMIN_BEARER_TOKEN`, OpenRouter keys, R2 credentials, database credentials, D1 tokens, and Vectorize tokens in Koyeb secrets.
- Route browser traffic through the HIVE-UI Cloudflare Function so the backend bearer token never enters the compiled frontend.
- Use explicit `CORS_ORIGINS` and `ALLOWED_HOSTS` in production.
- Leave API documentation disabled in production unless there is a temporary, controlled need.
- Rotate any credential immediately after accidental disclosure.
- Use `/livez` for liveness and `/readyz` for readiness. The detailed report is authenticated at `/v1/runtime/readiness`.

## Reporting

Report suspected vulnerabilities privately to the repository owner. Include the affected route, impact, and a minimal reproduction. Do not include live secrets or production data.
