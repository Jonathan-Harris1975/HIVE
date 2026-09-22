from __future__ import annotations

import io
import json

from botocore.exceptions import ClientError

from app.core.config import Settings
from app.storage.model_registry_pending import R2ModelRegistryPendingStore


class _MemoryR2Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    @staticmethod
    def _error(code: str, status: int, operation: str) -> ClientError:
        return ClientError(
            {
                "Error": {"Code": code, "Message": code},
                "ResponseMetadata": {"HTTPStatusCode": status},
            },
            operation,
        )

    def put_object(self, *, Key, Body, IfNoneMatch=None, **_kwargs):
        if IfNoneMatch == "*" and Key in self.objects:
            raise self._error("PreconditionFailed", 412, "PutObject")
        self.objects[Key] = bytes(Body)
        return {"ETag": "memory"}

    def list_objects_v2(self, *, Prefix, **_kwargs):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        return {
            "Contents": [{"Key": key, "Size": len(self.objects[key])} for key in keys],
            "IsTruncated": False,
        }

    def get_object(self, *, Key, **_kwargs):
        if Key not in self.objects:
            raise self._error("NoSuchKey", 404, "GetObject")
        body = self.objects[Key]
        return {"Body": io.BytesIO(body), "ContentLength": len(body)}

    def head_object(self, *, Key, **_kwargs):
        if Key not in self.objects:
            raise self._error("NotFound", 404, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_objects(self, *, Delete, **_kwargs):
        deleted = []
        for item in Delete["Objects"]:
            key = item["Key"]
            self.objects.pop(key, None)
            deleted.append({"Key": key})
        return {"Deleted": deleted, "Errors": []}


def _settings() -> Settings:
    return Settings(
        cf_r2_endpoint_url="https://test.r2.invalid",
        cf_r2_access_key_id="test-access-key",
        cf_r2_secret_access_key="test-secret-key",
        r2_bucket_meta_system="metasystem",
        r2_multi_bucket_write_enabled=True,
    )


def _operation(operation_id: str, queued_at: float, *, action: str = "upsert"):
    return {
        "operation_id": operation_id,
        "action": action,
        "category": "coding",
        "model_id": "sensitive/provider-model",
        "queued_at": queued_at,
        "model": (
            {
                "model_id": "sensitive/provider-model",
                "category": "coding",
                "score": queued_at,
                "provider": "private-provider",
                "notes": None,
                "registered_at": queued_at,
            }
            if action == "upsert"
            else None
        ),
        "attempts": 0,
        "last_error": None,
    }


def _store(client: _MemoryR2Client) -> R2ModelRegistryPendingStore:
    store = R2ModelRegistryPendingStore(_settings())
    store.storage._client = client
    return store


def test_r2_pending_operation_survives_a_completely_new_store_instance() -> None:
    client = _MemoryR2Client()
    first_instance = _store(client)
    operation = _operation("operation-one", 1.0)

    first_instance.persist(operation)

    assert all("sensitive/provider-model" not in key for key in client.objects)
    second_instance = _store(client)
    assert second_instance.load() == [operation]


def test_r2_claim_allows_only_one_reconciler_and_completion_removes_work() -> None:
    client = _MemoryR2Client()
    first_instance = _store(client)
    second_instance = _store(client)
    operation = _operation("operation-one", 1.0)
    first_instance.persist(operation)

    assert first_instance.claim(operation) is True
    assert second_instance.claim(operation) is False

    first_instance.complete(operation)

    assert second_instance.load() == []
    assert not any("/claims/" in key for key in client.objects)


def test_r2_latest_operation_wins_and_completion_removes_only_older_work() -> None:
    client = _MemoryR2Client()
    store = _store(client)
    stale_registration = _operation("operation-old", 1.0)
    latest_deletion = _operation("operation-new", 2.0, action="delete")
    store.persist(stale_registration)
    store.persist(latest_deletion)

    assert store.claim(stale_registration) is False
    assert store.claim(latest_deletion) is True
    store.complete(latest_deletion)

    assert store.load() == []


def test_r2_failure_update_keeps_the_same_durable_operation_identity() -> None:
    client = _MemoryR2Client()
    store = _store(client)
    operation = _operation("operation-one", 1.0)
    store.persist(operation)
    updated = {**operation, "attempts": 1, "last_error": "temporary d1 outage"}

    store.record_failure(updated)

    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0]["operation_id"] == "operation-one"
    assert loaded[0]["attempts"] == 1
    assert json.dumps(loaded[0])
