from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

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
                config=Config(signature_version="s3v4"),
                region_name=self.settings.r2_region or "auto",
            )
        return self._client

    def put_file(self, path: Path, key: str, content_type: str | None = None) -> StoredObject:
        digest = sha256_file(path)
        extra_args = {"ContentType": content_type} if content_type else {}
        try:
            self.client().upload_file(str(path), self.settings.cf_r2_bucket, key, ExtraArgs=extra_args)
        except (BotoCoreError, ClientError) as exc:
            raise RuntimeError(f"R2 upload failed for {key}: {exc}") from exc
        public_url = None
        if self.settings.cf_r2_public_base_url:
            public_url = f"{self.settings.cf_r2_public_base_url.rstrip('/')}/{key}"
        return StoredObject(
            key=key,
            bucket=self.settings.cf_r2_bucket,
            size_bytes=path.stat().st_size,
            sha256=digest,
            public_url=public_url,
        )

    def list_keys(self, prefix: str = "", limit: int = 1000) -> list[str]:
        response = self.client().list_objects_v2(
            Bucket=self.settings.cf_r2_bucket,
            Prefix=prefix,
            MaxKeys=limit,
        )
        return [item["Key"] for item in response.get("Contents", [])]


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
