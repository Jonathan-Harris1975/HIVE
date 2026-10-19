# HIVE repair handoff and owner verification

This runbook records the boundary between repository automation and external controls. A green workflow does not prove that the Kilo service, Koyeb runtime, GitHub environment settings, or cloud trust policy are configured correctly.

## Durable handoff behaviour

The autonomous repair workflow reserves a run-specific marker on its carrier PR before calling Kilo. An explicit HTTP 4xx rejection is recorded as retryable, with a maximum of two rejected attempts. A timeout or server error can have an ambiguous outcome, so its reservation stays pending and automation must not send a duplicate handoff. Inspect the Kilo service and carrier marker, then resolve the incident with the service owner before any replay.

If the handoff step fails, the workflow sends a correlated `kilo_repair_handoff_failed` event to the HIVE operations inbox. Event IDs are stable per workflow run. When D1 is enabled, the ingest endpoint returns success only after the event is persisted; a persistence failure returns HTTP 503 so the sender can retry. Local-development mode without D1 continues to use bounded process memory and reports `persisted: false`.

## Owner-only verification

- **Kilo:** Verify the repository secret `KILO_REPAIR_TRIGGER_URL` points to the intended HTTPS trigger, and have the Kilo service owner confirm the trigger authorises this HIVE repository and preserves the task contract. Do not broaden credentials to clear a 403.
- **HIVE operations inbox:** Verify GitHub secrets `OPS_ALERT_WEBHOOK_URL` and `OPS_ALERT_WEBHOOK_TOKEN` route to the HIVE `/ops/events` endpoint and match HIVE's `OPS_EVENT_INGEST_ENABLED` and `OPS_EVENT_INGEST_TOKEN` settings.
- **Durable event storage:** Verify HIVE's D1 account/database credentials, schema, and write access. Never expose credential values in workflow output.
- **Koyeb:** Verify the HIVE service, runtime health, and exact environment bindings through the Koyeb owner account. Repository code cannot prove deployed configuration.
- **OIDC:** Verify the cloud provider trust policy uses the GitHub Actions issuer, intended audience, HIVE repository identity, and only the required workflow plus `main` ref/environment. The HIVE OIDC workflow verifies GitHub's signed claims; it does not verify a cloud provider trust policy.
- **Live recovery:** After review and merge, run a controlled non-production matrix for explicit 403, timeout/ambiguous result, stale repository evidence, partial outage, duplicate event, and owner-approved replay. Confirm no duplicate Kilo task, durable correlated incident, independent health evidence, and closure only after authoritative recovery evidence.

Repository changes do not include secrets, account settings, Koyeb configuration, provider policy, production data, deployment, or live testing.
