# Shared inline Headroom compression

AIMS can use HIVE's existing inline Headroom installation through `POST /v1/compress`. This reuses the running HIVE service and avoids adding a paid companion service. No model inference or provider call is made by this endpoint.

The endpoint accepts only the existing AIMS service bearer key, already shared with HIVE for governed service operations. It does not accept a browser session or replace the HIVE admin authentication policy on other routes. Credentials and prompt bodies are not returned or added to deployment logs.

Requests are capped at 1.5 MB, 64 text messages and 262,144 characters (or the smaller configured Headroom limit). One compression worker may run per process; capacity remains held until that thread finishes even if its HTTP caller disconnects. Busy, disabled or failed compression returns an explicit failure, allowing the caller's existing recovery policy to preserve the original input.

HIVE controls compression settings. System/developer instructions and the latest message cannot change; message metadata is copied from the original. The existing Headroom wrapper rejects unsupported retrieval markers, malformed output and token inflation. No-saving results return original messages with an explicit skipped status. Response fields match AIMS's compression client contract.

`HEADROOM_BEACON=off` and `DO_NOT_TRACK=1` disable external anonymous uploads. Existing local savings metrics may remain enabled. Provider-recorded OpenRouter charges remain the authority for actual savings; synthetic deployment checks are not model spend.
