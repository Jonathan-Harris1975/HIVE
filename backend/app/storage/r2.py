from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, unquote

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings


_DEFAULT_PUBLIC_BASE = object()


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
    etag: str | None = None
    storage_class: str | None = None


@dataclass(frozen=True)
class ObjectListPage:
    objects: list[ObjectSummary]
    prefixes: list[str]
    next_cursor: str | None
    scanned_count: int
    truncated: bool


@dataclass(frozen=True)
class ObjectMetadata:
    key: str
    bucket: str
    size_bytes: int
    content_type: str | None = None
    last_modified: str | None = None
    etag: str | None = None
    cache_control: str | None = None
    content_disposition: str | None = None
    metadata: dict[str, str] | None = None
    public_url: str | None = None


@dataclass(frozen=True)
class ReadObject:
    key: str
    bucket: str
    content: bytes
    size_bytes: int
    content_type: str | None = None
    public_url: str | None = None
    last_modified: str | None = None
    etag: str | None = None


@dataclass(frozen=True)
class ObjectStream:
    key: str
    bucket: str
    body: BinaryIO | Any
    size_bytes: int
    content_type: str | None = None
    content_disposition: str | None = None
    last_modified: str | None = None
    etag: str | None = None


class R2Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.enabled = bool(
            settings.r2_endpoint_url
            and settings.cf_r2_access_key_id
            and settings.cf_r2_secret_access_key
            and settings.cf_r2_bucket
        )
        self.read_enabled = bool(settings.r2_read_credentials_configured)
        self._client = None
        self._read_client = None

    def client(self, *, read_only: bool = False):
        if read_only:
            if not self.read_enabled:
                raise RuntimeError("Cloudflare R2 multi-bucket read access is not configured")
            if self._read_client is None:
                self._read_client = self._build_client(
                    self.settings.r2_read_access_key_id,
                    self.settings.r2_read_secret_access_key,
                )
            return self._read_client

        if not self.enabled:
            raise RuntimeError("Cloudflare R2 is not configured")
        if self._client is None:
            self._client = self._build_client(
                self.settings.cf_r2_access_key_id,
                self.settings.cf_r2_secret_access_key,
            )
        return self._client

    def _build_client(self, access_key_id: str, secret_access_key: str):
        return boto3.client(
            "s3",
            endpoint_url=self.settings.r2_endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                connect_timeout=self.settings.r2_connect_timeout_seconds,
                read_timeout=self.settings.r2_read_timeout_seconds,
                retries={"max_attempts": self.settings.r2_max_attempts, "mode": "standard"},
                s3={"addressing_style": self.settings.r2_addressing_style},
            ),
            region_name=self.settings.r2_region or "auto",
        )

    def public_url_for_key(
        self,
        key: str,
        public_base_url: str | None | object = _DEFAULT_PUBLIC_BASE,
    ) -> str | None:
        base = (
            self.settings.cf_r2_public_base_url
            if public_base_url is _DEFAULT_PUBLIC_BASE
            else public_base_url
        )
        clean_key = (key or "").replace("\\", "/").lstrip("/")
        decoded_key = unquote(clean_key)
        if not clean_key or any(part in {"", ".", ".."} for part in decoded_key.split("/")):
            return None
        if isinstance(base, str) and base:
            return f"{base.rstrip('/')}/{quote(clean_key, safe='/~')}"
        return None

    def put_file(self, path: Path, key: str, content_type: str | None = None) -> StoredObject:
        digest = sha256_file(path)
        extra_args = {"ContentType": content_type} if content_type else {}
        try:
            self.client().upload_file(
                str(path),
                self.settings.cf_r2_bucket,
                key,
                ExtraArgs=extra_args,
            )
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
        *,
        read_only: bool = False,
    ) -> list[ObjectSummary]:
        return self.list_objects_page(
            prefix=prefix,
            limit=limit,
            bucket=bucket,
            public_base_url=public_base_url,
            read_only=read_only,
        ).objects

    def list_objects_page(
        self,
        *,
        prefix: str = "",
        limit: int = 100,
        bucket: str | None = None,
        public_base_url: str | None = None,
        cursor: str | None = None,
        delimiter: str | None = "/",
        search: str | None = None,
        read_only: bool = False,
        max_scan_keys: int | None = None,
    ) -> ObjectListPage:
        safe_limit = max(1, min(int(limit), 1000))
        bucket_name = bucket or self.settings.cf_r2_bucket
        needle = (search or "").strip().lower()
        max_scan = max(
            safe_limit,
            min(
                int(max_scan_keys or self.settings.r2_multi_bucket_max_scan_keys),
                50_000,
            ),
        )
        objects: list[ObjectSummary] = []
        prefixes: list[str] = []
        seen_prefixes: set[str] = set()
        continuation = cursor or None
        scanned = 0
        truncated = False

        try:
            while True:
                request: dict[str, Any] = {
                    "Bucket": bucket_name,
                    "Prefix": prefix,
                    "MaxKeys": safe_limit,
                }
                if delimiter:
                    request["Delimiter"] = delimiter
                if continuation:
                    request["ContinuationToken"] = continuation
                response = self.client(read_only=read_only).list_objects_v2(**request)

                for common in response.get("CommonPrefixes", []):
                    common_prefix = common.get("Prefix")
                    if common_prefix and common_prefix not in seen_prefixes:
                        seen_prefixes.add(common_prefix)
                        prefixes.append(common_prefix)

                for item in response.get("Contents", []):
                    key = item.get("Key")
                    if not key:
                        continue
                    scanned += 1
                    if needle and needle not in key.lower():
                        if scanned >= max_scan:
                            break
                        continue
                    objects.append(
                        ObjectSummary(
                            key=key,
                            size_bytes=int(item.get("Size") or 0),
                            last_modified=_isoformat(item.get("LastModified")),
                            public_url=self.public_url_for_key(
                                key,
                                public_base_url=public_base_url,
                            ),
                            etag=_clean_etag(item.get("ETag")),
                            storage_class=item.get("StorageClass"),
                        )
                    )
                    if len(objects) >= safe_limit:
                        break

                next_cursor = response.get("NextContinuationToken")
                is_truncated = bool(response.get("IsTruncated") and next_cursor)
                reached_limit = len(objects) >= safe_limit
                reached_scan_limit = scanned >= max_scan
                if reached_limit or reached_scan_limit or not is_truncated:
                    truncated = bool(is_truncated or reached_scan_limit)
                    continuation = str(next_cursor) if is_truncated and next_cursor else None
                    break
                continuation = str(next_cursor)
        except ClientError as exc:
            raise RuntimeError(
                f"R2 list failed for bucket {bucket_name!r} prefix {prefix!r}: "
                f"{_format_client_error(exc)}"
            ) from exc
        except BotoCoreError as exc:
            raise RuntimeError(
                f"R2 list failed for bucket {bucket_name!r} prefix {prefix!r}: {exc}"
            ) from exc

        return ObjectListPage(
            objects=objects[:safe_limit],
            prefixes=prefixes,
            next_cursor=continuation,
            scanned_count=scanned,
            truncated=truncated,
        )

    def head_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        public_base_url: str | None = None,
        read_only: bool = False,
    ) -> ObjectMetadata:
        if not key:
            raise ValueError("Object key is required")
        bucket_name = bucket or self.settings.cf_r2_bucket
        try:
            head = self.client(read_only=read_only).head_object(Bucket=bucket_name, Key=key)
        except ClientError as exc:
            _raise_read_error("metadata", bucket_name, key, exc)
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 metadata failed for {bucket_name}/{key}: {exc}") from exc

        return ObjectMetadata(
            key=key,
            bucket=bucket_name,
            size_bytes=int(head.get("ContentLength") or 0),
            content_type=head.get("ContentType"),
            last_modified=_isoformat(head.get("LastModified")),
            etag=_clean_etag(head.get("ETag")),
            cache_control=head.get("CacheControl"),
            content_disposition=head.get("ContentDisposition"),
            metadata={str(k): str(v) for k, v in (head.get("Metadata") or {}).items()},
            public_url=self.public_url_for_key(key, public_base_url=public_base_url),
        )

    def read_object(
        self,
        key: str,
        max_bytes: int,
        *,
        bucket: str | None = None,
        public_base_url: str | None = None,
        read_only: bool = False,
    ) -> ReadObject:
        if not key:
            raise ValueError("Object key is required")
        bucket_name = bucket or self.settings.cf_r2_bucket
        try:
            head = self.client(read_only=read_only).head_object(Bucket=bucket_name, Key=key)
            size_bytes = int(head.get("ContentLength") or 0)
            if size_bytes > max_bytes:
                raise ValueError(
                    f"Object is {size_bytes} bytes; max read size is {max_bytes} bytes"
                )
            response = self.client(read_only=read_only).get_object(Bucket=bucket_name, Key=key)
            content = response["Body"].read(max_bytes + 1)
        except ValueError:
            raise
        except ClientError as exc:
            _raise_read_error("read", bucket_name, key, exc)
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 read failed for {bucket_name}/{key}: {exc}") from exc

        if len(content) > max_bytes:
            raise ValueError(f"Object exceeds max read size of {max_bytes} bytes")
        return ReadObject(
            key=key,
            bucket=bucket_name,
            content=content,
            size_bytes=size_bytes,
            content_type=head.get("ContentType") or response.get("ContentType"),
            public_url=self.public_url_for_key(key, public_base_url=public_base_url),
            last_modified=_isoformat(head.get("LastModified")),
            etag=_clean_etag(head.get("ETag")),
        )

    def open_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        max_bytes: int | None = None,
        read_only: bool = False,
    ) -> ObjectStream:
        if not key:
            raise ValueError("Object key is required")
        bucket_name = bucket or self.settings.cf_r2_bucket
        try:
            response = self.client(read_only=read_only).get_object(Bucket=bucket_name, Key=key)
        except ClientError as exc:
            _raise_read_error("download", bucket_name, key, exc)
        except BotoCoreError as exc:
            raise RuntimeError(f"R2 download failed for {bucket_name}/{key}: {exc}") from exc

        size_bytes = int(response.get("ContentLength") or 0)
        if max_bytes is not None and size_bytes > max_bytes:
            response["Body"].close()
            raise ValueError(
                f"Object is {size_bytes} bytes; max download size is {max_bytes} bytes"
            )
        return ObjectStream(
            key=key,
            bucket=bucket_name,
            body=response["Body"],
            size_bytes=size_bytes,
            content_type=response.get("ContentType"),
            content_disposition=response.get("ContentDisposition"),
            last_modified=_isoformat(response.get("LastModified")),
            etag=_clean_etag(response.get("ETag")),
        )


def _isoformat(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _clean_etag(value: Any) -> str | None:
    if not value:
        return None
    return str(value).strip('"')


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _raise_read_error(operation: str, bucket: str, key: str, exc: ClientError) -> None:
    error = (exc.response or {}).get("Error", {})
    code = str(error.get("Code") or "").lower()
    status_code = (exc.response or {}).get("ResponseMetadata", {}).get("HTTPStatusCode")
    if code in {"nosuchkey", "notfound", "404"} or status_code == 404:
        raise FileNotFoundError(f"R2 object not found: {bucket}/{key}") from exc
    raise RuntimeError(
        f"R2 {operation} failed for {bucket}/{key}: {_format_client_error(exc)}"
    ) from exc


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
