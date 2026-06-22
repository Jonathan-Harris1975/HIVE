# HIVE security policy

**Status:** Production-controlled  
**Last reviewed:** 22 June 2026

## Supported release

The current `main` branch and latest production deployment are supported.

## Security model

HIVE is a private operator API. Production requests use a strong bearer token, HTTPS-only origins and trusted-host validation. Provider, database and storage secrets are held by Koyeb and are never returned to HIVE-UI. The Cloudflare Pages proxy stores the backend token, while the browser receives only a signed, `HttpOnly` operator session.

The `hive` upload bucket is the only write-enabled storage lane. Additional R2 buckets are accessed with a separate read-only credential. File reads, extraction, ZIP traversal, request bodies, chunk counts and model context are bounded.

## Required practices

- Rotate the HIVE admin token and provider credentials when exposure is suspected.
- Scope R2 credentials to the minimum buckets and permissions.
- Keep production API documentation disabled unless temporarily required for diagnosis.
- Review dependency and container gates before deployment.
- Never place secrets in repository files, screenshots, logs or client-side environment variables.

Report suspected vulnerabilities privately to the repository owner. Include the affected endpoint, reproduction steps and impact without publishing live credentials.
