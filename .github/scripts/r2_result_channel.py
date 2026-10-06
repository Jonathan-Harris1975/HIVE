#!/usr/bin/env python3
"""Create and consume short-lived Cloudflare R2 result channels for CI.

The presigned PUT URL is a temporary single-object capability. Never print it to
logs without first masking it in GitHub Actions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _encode_path(bucket: str, key: str) -> str:
    parts = [bucket, *key.strip("/").split("/")]
    if not key.strip("/") or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError("invalid R2 bucket/key")
    return "/" + "/".join(urllib.parse.quote(part, safe="-._~") for part in parts)


def _signing_key(secret: str, date: str, region: str = "auto", service: str = "s3") -> bytes:
    k_date = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def presign_put(bucket: str, key: str, expires: int) -> str:
    if not 60 <= expires <= 3600:
        raise RuntimeError("presigned URL expiry must be between 60 and 3600 seconds")
    account = _required("R2_ACCOUNT_ID")
    access = _required("R2_ACCESS_KEY_ID")
    secret = _required("R2_SECRET_ACCESS_KEY")
    host = f"{account}.r2.cloudflarestorage.com"
    path = _encode_path(bucket, key)

    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    short = now.strftime("%Y%m%d")
    scope = f"{short}/auto/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Content-Sha256": "UNSIGNED-PAYLOAD",
        "X-Amz-Credential": f"{access}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "content-type;host",
    }
    canonical_query = urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)
    canonical_request = "\n".join([
        "PUT",
        path,
        canonical_query,
        f"content-type:application/json\nhost:{host}\n",
        "content-type;host",
        "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(secret, short),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()
    query["X-Amz-Signature"] = signature
    return f"https://{host}{path}?" + urllib.parse.urlencode(sorted(query.items()), quote_via=urllib.parse.quote)


def _signed_request(method: str, bucket: str, key: str) -> urllib.request.Request:
    account = _required("R2_ACCOUNT_ID")
    access = _required("R2_ACCESS_KEY_ID")
    secret = _required("R2_SECRET_ACCESS_KEY")
    host = f"{account}.r2.cloudflarestorage.com"
    path = _encode_path(bucket, key)
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    short = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(b"").hexdigest()
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    signed_names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical_request = "\n".join([method, path, "", canonical_headers, signed_names, payload_hash])
    scope = f"{short}/auto/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(_signing_key(secret, short), string_to_sign.encode(), hashlib.sha256).hexdigest()
    req_headers = {k: v for k, v in headers.items() if k != "host"}
    req_headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signed_names}, Signature={signature}"
    )
    return urllib.request.Request(f"https://{host}{path}", headers=req_headers, method=method)


def wait_get(bucket: str, key: str, output: Path, timeout: int, interval: int) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(_signed_request("GET", bucket, key), timeout=30) as response:
                if response.status == 200:
                    data = response.read()
                    if len(data) > 262_144:
                        raise RuntimeError("Council result exceeds 256 KiB")
                    output.write_bytes(data)
                    return
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                body = exc.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"R2 GET failed with HTTP {exc.code}: {body}") from exc
        if time.monotonic() >= deadline:
            raise RuntimeError("timed out waiting for final Council result in R2")
        time.sleep(interval)


def validate_result(path: Path, repository: str, sha: str, run_id: int, run_attempt: int) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "kind": "council-final",
        "repository": repository,
        "sha": sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "certification_complete": True,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeError(f"Council result field {key!r} does not match the exact run")
    if payload.get("disposition") not in {"READY", "HOLD", "PENDING_MINIMUM_AGE"}:
        raise RuntimeError("Council result disposition is invalid")
    if not isinstance(payload.get("evidence"), list):
        raise RuntimeError("Council result evidence must be a list")
    if not isinstance(payload.get("blockers"), list):
        raise RuntimeError("Council result blockers must be a list")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("presign-put")
    p.add_argument("--bucket", default=os.environ.get("R2_BUCKET", "hive-repositories"))
    p.add_argument("--key", required=True)
    p.add_argument("--expires", type=int, default=3600)

    w = sub.add_parser("wait-get")
    w.add_argument("--bucket", default=os.environ.get("R2_BUCKET", "hive-repositories"))
    w.add_argument("--key", required=True)
    w.add_argument("--output", type=Path, required=True)
    w.add_argument("--timeout", type=int, default=2700)
    w.add_argument("--interval", type=int, default=15)

    v = sub.add_parser("validate-result")
    v.add_argument("json_file", type=Path)
    v.add_argument("--repository", required=True)
    v.add_argument("--sha", required=True)
    v.add_argument("--run-id", type=int, required=True)
    v.add_argument("--run-attempt", type=int, required=True)

    args = parser.parse_args()
    if args.command == "presign-put":
        print(presign_put(args.bucket, args.key, args.expires))
    elif args.command == "wait-get":
        wait_get(args.bucket, args.key, args.output, args.timeout, args.interval)
    else:
        validate_result(args.json_file, args.repository, args.sha, args.run_id, args.run_attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
