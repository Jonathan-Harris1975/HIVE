#!/usr/bin/env python3
"""Minimal Cloudflare R2 evidence writer using AWS Signature Version 4.

This helper intentionally uses only the Python standard library so GitHub Actions
does not need a package install merely to persist CI evidence.

Accepted credentials:
- R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
- CF_R2_ACCOUNT_ID / CF_R2_ACCESS_KEY_ID / CF_R2_SECRET_ACCESS_KEY

The default bucket is hive-repositories.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _env(primary: str, fallback: str) -> str:
    return os.getenv(primary, "").strip() or os.getenv(fallback, "").strip()


def _sign(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = _sign(("AWS4" + secret).encode("utf-8"), date_stamp)
    key_region = _sign(key_date, region)
    key_service = _sign(key_region, service)
    return _sign(key_service, "aws4_request")


def _normalise_key(key: str) -> str:
    clean = key.strip("/")
    if not clean or ".." in clean.split("/"):
        raise ValueError("R2 object key must be non-empty and must not contain '..' path segments")
    return clean


def put_object(*, file_path: str, object_key: str, content_type: str, bucket: str) -> str:
    account_id = _env("R2_ACCOUNT_ID", "CF_R2_ACCOUNT_ID")
    access_key = _env("R2_ACCESS_KEY_ID", "CF_R2_ACCESS_KEY_ID")
    secret_key = _env("R2_SECRET_ACCESS_KEY", "CF_R2_SECRET_ACCESS_KEY")

    missing = [
        name
        for name, value in (
            ("R2_ACCOUNT_ID/CF_R2_ACCOUNT_ID", account_id),
            ("R2_ACCESS_KEY_ID/CF_R2_ACCESS_KEY_ID", access_key),
            ("R2_SECRET_ACCESS_KEY/CF_R2_SECRET_ACCESS_KEY", secret_key),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("Missing required R2 credential environment: " + ", ".join(missing))

    object_key = _normalise_key(object_key)
    bucket = bucket.strip()
    if not bucket:
        raise ValueError("R2 bucket must be non-empty")

    with open(file_path, "rb") as handle:
        payload = handle.read()

    payload_hash = hashlib.sha256(payload).hexdigest()
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region = "auto"
    service = "s3"

    host = f"{account_id}.r2.cloudflarestorage.com"
    encoded_path = "/" + urllib.parse.quote(bucket, safe="") + "/" + "/".join(
        urllib.parse.quote(part, safe="~") for part in object_key.split("/")
    )
    endpoint = f"https://{host}{encoded_path}"

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "content-type;host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            "PUT",
            encoded_path,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )

    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            algorithm,
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, date_stamp, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    authorization = (
        f"{algorithm} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    request = urllib.request.Request(
        endpoint,
        data=payload,
        method="PUT",
        headers={
            "Authorization": authorization,
            "Content-Type": content_type,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"R2 PUT failed with HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"R2 PUT failed with HTTP {exc.code}: {detail}") from exc

    return object_key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--content-type", default="application/octet-stream")
    parser.add_argument("--bucket", default=os.getenv("R2_EVIDENCE_BUCKET", "hive-repositories"))
    args = parser.parse_args(argv)

    try:
        key = put_object(
            file_path=args.file,
            object_key=args.key,
            content_type=args.content_type,
            bucket=args.bucket,
        )
    except Exception as exc:
        print(f"R2 evidence persistence failed: {exc}", file=sys.stderr)
        return 1

    print(f"Persisted R2 evidence object: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
