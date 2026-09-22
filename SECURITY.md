# HIVE security policy

**Status:** Production-controlled  
**Last reviewed:** 22 September 2026

## Supported release

The current `main` branch and latest production deployment are supported.

## Security model

HIVE is a private operator API. Production requests use a strong bearer token, HTTPS-only origins and trusted-host validation. Provider, database and storage secrets are held by Koyeb and are never returned to HIVE-UI. The Cloudflare Pages proxy stores the backend token, while the browser receives only a signed, `HttpOnly` operator session.

R2 writes are limited to governed private lanes required by HIVE, including uploads, repository artefacts and the internal `meta_system` operation/heartbeat lane. Other ecosystem buckets use scoped read access. Model Registry pending operations use hashed object-key partitions, bounded payloads and private R2 storage; credentials are never included in keys, diagnostics or response payloads. File reads, extraction, ZIP traversal, request bodies, chunk counts and model context are bounded.


## Authentication and proxy boundary

Administrative bearer authentication uses constant-time token comparison and process-local failure throttling. Failed authentication is counted in two scopes: `(source IP, token fingerprint)` and source IP. Token bucket keys contain a truncated SHA-256 fingerprint rather than any literal token prefix. This prevents simple prefix rotation from resetting the limiter and avoids retaining credential fragments in memory.

The development sentinel token is accepted only from loopback peers (plus the controlled test client in `APP_ENV=test`). Uvicorn's forwarded-header trust defaults to `127.0.0.1`, not `*`. On Koyeb, where the platform documents that the last `X-Forwarded-For` value is the certified connecting client address, the auth limiter validates and uses only that final address. Do not set `FORWARDED_ALLOW_IPS=*` unless the upstream proxy contract has been independently verified and the service cannot be reached around that proxy.

## Required practices

- Rotate the HIVE admin token and provider credentials when exposure is suspected.
- Scope R2 credentials to the minimum buckets and permissions.
- Keep production API documentation disabled unless temporarily required for diagnosis.
- Review dependency and container gates before deployment.
- Never place secrets in repository files, screenshots, logs or client-side environment variables.

Report suspected vulnerabilities privately to the repository owner. Include the affected endpoint, reproduction steps and impact without publishing live credentials.

CI scans the actual production image for OS and application-library vulnerabilities with an immutable Trivy action reference. Fixable `HIGH` and `CRITICAL` findings fail the Docker job. `ignore-unfixed` applies only where no upstream fix exists; it is not permission to suppress findings broadly, and the human-readable report is retained for 90 days.
