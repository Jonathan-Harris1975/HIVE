from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from app.core.config import Settings
from app.storage.r2 import R2Storage


logger = logging.getLogger("uvicorn.error.hive.model_registry_pending")


class DurablePendingStoreError(RuntimeError):
    """Raised when the externally durable pending-operation store is unavailable."""


@dataclass(frozen=True)
class PendingStoreDiagnostics:
    backend: str
    enabled: bool
    lane: str
    bucket_configured: bool
    prefix: str


class R2ModelRegistryPendingStore:
    """Private R2 operation log used when Model Registry D1 writes cannot commit.

    Every operation has its own immutable identity and a private object key that
    hashes the model key. Reconciliation claims use an R2 conditional create so
    separate HIVE instances cannot intentionally apply the same operation at the
    same time. D1 upserts/deletes remain idempotent as the final safety layer.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.storage = R2Storage(settings)
        self.lane_name = settings.model_registry_pending_r2_lane.strip().lower()
        lane = settings.internal_r2_lane(self.lane_name)
        self.bucket = str((lane or {}).get("bucket") or "").strip()
        self.prefix = settings.model_registry_pending_r2_prefix.strip().strip("/")
        self.max_bytes = settings.model_registry_pending_max_bytes
        self.max_operations = settings.model_registry_pending_max_operations
        self.lease_seconds = settings.model_registry_pending_lease_seconds
        self.owner_id = uuid.uuid4().hex
        self.enabled = bool(
            self.prefix
            and self.bucket
            and self.storage.write_enabled
            and bool((lane or {}).get("writable"))
        )

    def safe_config(self) -> dict[str, object]:
        return {
            "backend": "r2",
            "enabled": self.enabled,
            "lane": self.lane_name,
            "bucket_configured": bool(self.bucket),
            "prefix": self.prefix,
        }

    @staticmethod
    def _operation_identity(operation: dict[str, object]) -> tuple[str, str, str]:
        operation_id = str(operation.get("operation_id") or "").strip()
        category = str(operation.get("category") or "").strip()
        model_id = str(operation.get("model_id") or "").strip()
        if not operation_id or not category or not model_id:
            raise DurablePendingStoreError("Pending operation identity is incomplete")
        return operation_id, category, model_id

    def _model_digest(self, operation: dict[str, object]) -> str:
        _operation_id, category, model_id = self._operation_identity(operation)
        return hashlib.sha256(f"{category}\0{model_id}".encode("utf-8")).hexdigest()

    def _operation_key(self, operation: dict[str, object]) -> str:
        operation_id, _category, _model_id = self._operation_identity(operation)
        return f"{self.prefix}/pending/{self._model_digest(operation)}/{operation_id}.json"

    def _claim_key(self, operation: dict[str, object]) -> str:
        operation_id, _category, _model_id = self._operation_identity(operation)
        return f"{self.prefix}/claims/{operation_id}.json"

    def _client(self):
        if not self.enabled:
            raise DurablePendingStoreError(
                "Model Registry R2 pending-operation storage is not configured"
            )
        return self.storage.client(read_only=False)

    def _serialise(self, operation: dict[str, object]) -> bytes:
        payload = json.dumps(
            {"schema_version": 1, "operation": operation},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > self.max_bytes:
            raise DurablePendingStoreError(
                f"Pending operation exceeds the configured {self.max_bytes}-byte limit"
            )
        return payload

    def persist(self, operation: dict[str, object]) -> None:
        key = self._operation_key(operation)
        try:
            self._client().put_object(
                Bucket=self.bucket,
                Key=key,
                Body=self._serialise(operation),
                ContentType="application/json",
                CacheControl="no-store",
                Metadata={"operation-id": str(operation["operation_id"])},
            )
        except (BotoCoreError, ClientError, OSError, ValueError) as exc:
            raise DurablePendingStoreError(
                f"R2 pending-operation write failed ({_safe_error(exc)})"
            ) from exc

    def load(self) -> list[dict[str, object]]:
        objects = self._load_prefix(f"{self.prefix}/pending/")
        operations: list[dict[str, object]] = []
        for key, payload in objects:
            operation = payload.get("operation") if isinstance(payload, dict) else None
            if not isinstance(operation, dict):
                raise DurablePendingStoreError(f"Invalid pending-operation object: {key}")
            self._operation_identity(operation)
            operations.append(dict(operation))
        return operations

    def claim(self, operation: dict[str, object]) -> bool:
        operation_key = self._operation_key(operation)
        if not self._object_exists(operation_key):
            return False

        latest = self._latest_for_model(operation)
        if latest is None or latest.get("operation_id") != operation.get("operation_id"):
            return False

        claim_key = self._claim_key(operation)
        now = time.time()
        body = json.dumps(
            {
                "schema_version": 1,
                "operation_id": operation["operation_id"],
                "owner_id": self.owner_id,
                "created_at": now,
                "expires_at": now + self.lease_seconds,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            self._client().put_object(
                Bucket=self.bucket,
                Key=claim_key,
                Body=body,
                ContentType="application/json",
                CacheControl="no-store",
                IfNoneMatch="*",
            )
            return True
        except ClientError as exc:
            if not _is_precondition_failure(exc):
                raise DurablePendingStoreError(
                    f"R2 reconciliation claim failed ({_safe_error(exc)})"
                ) from exc
        except (BotoCoreError, OSError, ValueError) as exc:
            raise DurablePendingStoreError(
                f"R2 reconciliation claim failed ({_safe_error(exc)})"
            ) from exc

        if not self._claim_expired(claim_key, now):
            return False
        self._delete_keys([claim_key])
        try:
            self._client().put_object(
                Bucket=self.bucket,
                Key=claim_key,
                Body=body,
                ContentType="application/json",
                CacheControl="no-store",
                IfNoneMatch="*",
            )
            return True
        except ClientError as exc:
            if _is_precondition_failure(exc):
                return False
            raise DurablePendingStoreError(
                f"R2 reconciliation claim retry failed ({_safe_error(exc)})"
            ) from exc
        except (BotoCoreError, OSError, ValueError) as exc:
            raise DurablePendingStoreError(
                f"R2 reconciliation claim retry failed ({_safe_error(exc)})"
            ) from exc

    def complete(self, operation: dict[str, object]) -> None:
        cutoff = _operation_order(operation)
        keys = [self._claim_key(operation)]
        for key, payload in self._load_prefix(
            f"{self.prefix}/pending/{self._model_digest(operation)}/"
        ):
            candidate = payload.get("operation") if isinstance(payload, dict) else None
            if isinstance(candidate, dict) and _operation_order(candidate) <= cutoff:
                keys.append(key)
        self._delete_keys(keys)

    def record_failure(self, operation: dict[str, object]) -> None:
        self.persist(operation)

    def release_claim(self, operation: dict[str, object]) -> None:
        self._delete_keys([self._claim_key(operation)])

    def discard(self, operation: dict[str, object]) -> None:
        self._delete_keys([self._operation_key(operation), self._claim_key(operation)])

    def _latest_for_model(self, operation: dict[str, object]) -> dict[str, object] | None:
        candidates: list[dict[str, object]] = []
        for _key, payload in self._load_prefix(
            f"{self.prefix}/pending/{self._model_digest(operation)}/"
        ):
            candidate = payload.get("operation") if isinstance(payload, dict) else None
            if isinstance(candidate, dict):
                candidates.append(candidate)
        return max(candidates, key=_operation_order, default=None)

    def _object_exists(self, key: str) -> bool:
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise DurablePendingStoreError(
                f"R2 pending-operation lookup failed ({_safe_error(exc)})"
            ) from exc
        except BotoCoreError as exc:
            raise DurablePendingStoreError(
                f"R2 pending-operation lookup failed ({_safe_error(exc)})"
            ) from exc

    def _claim_expired(self, key: str, now: float) -> bool:
        try:
            response = self._client().get_object(Bucket=self.bucket, Key=key)
            content = response["Body"].read(self.max_bytes + 1)
            if len(content) > self.max_bytes:
                return False
            payload = json.loads(content.decode("utf-8"))
            return float(payload.get("expires_at") or 0) <= now
        except (BotoCoreError, ClientError, OSError, UnicodeDecodeError, ValueError, TypeError):
            return False

    def _load_prefix(self, prefix: str) -> list[tuple[str, dict[str, Any]]]:
        client = self._client()
        continuation: str | None = None
        keys: list[str] = []
        try:
            while True:
                request: dict[str, object] = {
                    "Bucket": self.bucket,
                    "Prefix": prefix,
                    "MaxKeys": min(1000, self.max_operations),
                }
                if continuation:
                    request["ContinuationToken"] = continuation
                response = client.list_objects_v2(**request)
                for item in response.get("Contents", []):
                    key = str(item.get("Key") or "")
                    if key:
                        keys.append(key)
                    if len(keys) > self.max_operations:
                        raise DurablePendingStoreError(
                            "Model Registry pending-operation limit exceeded"
                        )
                continuation_raw = response.get("NextContinuationToken")
                if not response.get("IsTruncated") or not continuation_raw:
                    break
                continuation = str(continuation_raw)

            payloads: list[tuple[str, dict[str, Any]]] = []
            for key in keys:
                response = client.get_object(Bucket=self.bucket, Key=key)
                size = int(response.get("ContentLength") or 0)
                if size > self.max_bytes:
                    raise DurablePendingStoreError(
                        f"Pending-operation object exceeds size limit: {key}"
                    )
                content = response["Body"].read(self.max_bytes + 1)
                if len(content) > self.max_bytes:
                    raise DurablePendingStoreError(
                        f"Pending-operation object exceeds size limit: {key}"
                    )
                decoded = json.loads(content.decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise DurablePendingStoreError(f"Invalid pending-operation object: {key}")
                payloads.append((key, decoded))
            return payloads
        except DurablePendingStoreError:
            raise
        except (BotoCoreError, ClientError, OSError, UnicodeDecodeError, ValueError) as exc:
            raise DurablePendingStoreError(
                f"R2 pending-operation read failed ({_safe_error(exc)})"
            ) from exc

    def _delete_keys(self, keys: list[str]) -> None:
        unique = sorted({key for key in keys if key})
        if not unique:
            return
        try:
            for offset in range(0, len(unique), 1000):
                batch = unique[offset : offset + 1000]
                response = self._client().delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": False},
                )
                errors = response.get("Errors") or []
                if errors:
                    codes = sorted({str(item.get("Code") or "unknown") for item in errors})
                    raise DurablePendingStoreError(
                        f"R2 pending-operation cleanup failed ({','.join(codes)})"
                    )
        except DurablePendingStoreError:
            raise
        except (BotoCoreError, ClientError, OSError, ValueError) as exc:
            raise DurablePendingStoreError(
                f"R2 pending-operation cleanup failed ({_safe_error(exc)})"
            ) from exc


def _operation_order(operation: dict[str, object]) -> tuple[float, str]:
    raw_queued_at = operation.get("queued_at")
    try:
        queued_at = (
            float(raw_queued_at) if isinstance(raw_queued_at, (int, float, str)) else 0.0
        )
    except (TypeError, ValueError):
        queued_at = 0.0
    return queued_at, str(operation.get("operation_id") or "")


def _is_precondition_failure(exc: ClientError) -> bool:
    response = exc.response or {}
    code = str(response.get("Error", {}).get("Code") or "").lower()
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"preconditionfailed", "conditionalrequestconflict", "412"} or status == 412


def _is_not_found(exc: ClientError) -> bool:
    response = exc.response or {}
    code = str(response.get("Error", {}).get("Code") or "").lower()
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in {"nosuchkey", "notfound", "404"} or status == 404


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        response = exc.response or {}
        code = str(response.get("Error", {}).get("Code") or "client_error")
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return f"{code}; status={status or 'unknown'}"
    return type(exc).__name__
