from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings


@dataclass(frozen=True)
class StoredObject:
    key: str
    bucket: str
    size_bytes: int
    sha256: str
    public_url: str | None = None


@dataclass(frozen=True)
class ObjectSummary:
    key: str
    size_bytes: int
    last_modified: str | None = None
    public_url: str | None = None


@dataclass(frozen=True)
class ReadObject:
    key: str
    bucket: str
    content: bytes
    size_bytes: int
    content_type: str | None = None
    public_url: str | None = None


class R2Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(
            settings.r2_endpoint_url
            and settings.cf_r2_access_key_id
            and settings.cf_r2_secret_access_key
            and settings.cf_r2_bucket
        )
        self._client = None

    def client(self):
        if not self.enabled:
            raise RuntimeError("Cloudflare R2 is not configured")
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.settings.r2_endpoint_url,
                aws_access_key_id=self.settings.cf_r2_access_key_id,
                aws_secret_access_key=self.settings.cf_r2_secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=self.settings.r2_connect_timeout_seconds,
                    read_timeout=self.settings.r2_read_timeout_seconds,
                    retries={"max_attempts": self.settings.r2_max_attempts, "mode": "standard"},
                    s3={"addressing_style": self.settings.r2_addressing_style},
                ),
                region_name=self.settings.r2_region or "auto",
            )
        return self._client

    def public_url_for_key(self, key: str, public_base_url: str | None = None) -> str | None:
        base = public_base_url or self.settings.cf_r2_public_base_url
        if base:
            return f"{base.rstrip('/')}/{key}"
        return None

    def put_file(self, path: Path, key: str, content_type: str | None = None) -> StoredObject:
        digest = sha256_file(path)
        extra_args = {"ContentType": content_type} if content_type else {}
        try:
            self.client().upload_file(str(path), self.settings.cf_r2_bucket, key, ExtraArgs=extra_args)
        except ClientError as exc:
            raise RuntimeError(f"R2 upload failed for {key}: {_format_client_error(exc)}") from exc
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 upload failed for {key}: {exc}") from exc
        return StoredObject(
            key=key,
            bucket=self.settings.cf_r2_bucket,
            size_bytes=path.stat().st_size,
            sha256=digest,
            public_url=self.public_url_for_key(key),
        )

    def list_keys(self, prefix: str = "", limit: int = 1000) -> list[str]:
        return [item.key for item in self.list_objects(prefix=prefix, limit=limit)]

    def list_objects(
        self,
        prefix: str = "",
        limit: int = 100,
        bucket: str | None = None,
        public_base_url: str | None = None,
    ) -> list[ObjectSummary]:
        safe_limit = max(1, min(int(limit), 1000))
        bucket_name = bucket or self.settings.cf_r2_bucket
        try:
            response = self.client().list_objects_v2(
                Bucket=bucket_name,
                Prefix=prefix,
                MaxKeys=safe_limit,
            )
        except ClientError as exc:
            raise RuntimeError(f"R2 list failed for bucket {bucket_name!r} prefix {prefix!r}: {_format_client_error(exc)}") from exc
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 list failed for bucket {bucket_name!r} prefix {prefix!r}: {exc}") from exc

        objects: list[ObjectSummary] = []
        for item in response.get("Contents", []):
            key = item.get("Key")
            if not key:
                continue
            last_modified = _isoformat(item.get("LastModified"))
            objects.append(
                ObjectSummary(
                    key=key,
                    size_bytes=int(item.get("Size") or 0),
                    last_modified=last_modified,
                    public_url=self.public_url_for_key(key, public_base_url=public_base_url),
                )
            )
        return objects

    def read_object(self, key: str, max_bytes: int) -> ReadObject:
        if not key:
            raise ValueError("Object key is required")
        try:
            head = self.client().head_object(Bucket=self.settings.cf_r2_bucket, Key=key)
            size_bytes = int(head.get("ContentLength") or 0)
            if size_bytes > max_bytes:
                raise ValueError(f"Object is {size_bytes} bytes; max read size is {max_bytes} bytes")
            response = self.client().get_object(Bucket=self.settings.cf_r2_bucket, Key=key)
            content = response["Body"].read(max_bytes + 1)
        except ValueError:
            raise
        except ClientError as exc:
            raise RuntimeError(f"R2 read failed for {key}: {_format_client_error(exc)}") from exc
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 read failed for {key}: {exc}") from exc

        if len(content) > max_bytes:
            raise ValueError(f"Object exceeds max read size of {max_bytes} bytes")
        return ReadObject(
            key=key,
            bucket=self.settings.cf_r2_bucket,
            content=content,
            size_bytes=size_bytes,
            content_type=head.get("ContentType") or response.get("ContentType"),
            public_url=self.public_url_for_key(key),
        )


def _isoformat(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _format_client_error(exc: ClientError) -> str:
    error = (exc.response or {}).get("Error", {})
    code = error.get("Code") or "Unknown"
    message = error.get("Message") or str(exc)
    status_code = (exc.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
    request_id = (exc.response or {}).get("ResponseMetadata", {}).get("RequestId")
    parts = [f"code={code}", f"message={message}"]
    if status_code:
        parts.append(f"http_status={status_code}")
    if request_id:
        parts.append(f"request_id={request_id}")
    return "; ".join(parts)
