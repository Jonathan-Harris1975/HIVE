# HIVE v1.26.11 CI build-marker sync patch

Date: 22 June 2026

## Purpose

The fresh CI run failed because the runtime build marker is now:

```text
v1.26.11-env-split
```

but a set of backend tests still asserted older build markers.

## CI symptom

GitHub Actions backend job reported build-marker assertion failures such as:

```text
assert 'v1.26.11-env-split' == 'v1.26.10-chat-persistence-sync'
```

## Fix

Updated backend test expectations to match the current HIVE env-split build marker:

```text
v1.26.11-env-split
```

No production runtime code was rolled back.

## Validation

Local backend test suite result after patch:

```text
180 passed
```

## Notes

This patch should be applied on top of the HIVE v1.26.11 env-split backend branch.
