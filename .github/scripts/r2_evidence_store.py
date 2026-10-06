#!/usr/bin/env python3
"""Persist non-secret CI evidence to the existing Cloudflare R2 bucket.

The payload must already be sanitised. This helper only accepts JSON carrying the
exact repository, default-branch SHA, workflow run ID and run attempt so evidence
cannot be detached from the run that produced it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for R2 evidence persistence")
    return value


def _signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def _signed_request(method: str, url: str, body: bytes, content_type: str | None = None) -> urllib.request.Request:
    access_key = _required_env("R2_ACCESS_KEY_ID")
    secret_key = _required_env("R2_SECRET_ACCESS_KEY")
    region = os.environ.get("R2_REGION", "auto").strip() or "auto"
    service = "s3"

    parsed = urllib.parse.urlsplit(url)
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()

    headers: dict[str, str] = {
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        headers["content-type"] = content_type

    signed_header_names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in signed_header_names)
    signed_headers = ";".join(signed_header_names)
    canonical_query = parsed.query
    canonical_request = "\n".join(
        [
            method,
            parsed.path or "/",
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    scope = f"{short_date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        _signing_key(secret_key, short_date, region, service),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()

    request_headers = {name: value for name, value in headers.items() if name != "host"}
    request_headers["Authorization"] = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return urllib.request.Request(url, data=body if method == "PUT" else None, headers=request_headers, method=method)


def _request(request: urllib.request.Request, expected: tuple[int, ...]) -> None:
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if response.status not in expected:
                raise RuntimeError(f"R2 returned unexpected HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"R2 HTTP {exc.code}: {body}") from exc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_file", type=Path)
    parser.add_argument("--prefix", default=os.environ.get("R2_EVIDENCE_PREFIX", "ci-evidence"))
    args = parser.parse_args()

    payload = json.loads(args.json_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("evidence payload must be a JSON object")

    required = ("repository", "sha", "run_id", "run_attempt", "kind")
    missing = [name for name in required if not str(payload.get(name, "")).strip()]
    if missing:
        raise RuntimeError("evidence payload is missing: " + ", ".join(missing))

    repository = str(payload["repository"])
    sha = str(payload["sha"])
    run_id = str(payload["run_id"])
    run_attempt = str(payload["run_attempt"])
    kind = str(payload["kind"])
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha.lower()):
        raise RuntimeError("sha must be a full 40-character commit SHA")
    if not run_id.isdigit() or not run_attempt.isdigit():
        raise RuntimeError("run_id and run_attempt must be numeric")

    account_id = _required_env("R2_ACCOUNT_ID")
    bucket = os.environ.get("R2_BUCKET", "hive-repositories").strip() or "hive-repositories"
    repo_key = repository.replace("/", "__")
    prefix = args.prefix.strip("/")
    key = f"{prefix}/{repo_key}/{kind}/{sha}/{run_id}-{run_attempt}.json"
    path = "/" + "/".join(urllib.parse.quote(part, safe="-._~") for part in [bucket, *key.split("/")])
    url = f"https://{account_id}.r2.cloudflarestorage.com{path}"

    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    _request(_signed_request("PUT", url, body, "application/json"), (200,))
    _request(_signed_request("HEAD", url, b""), (200,))
    print(json.dumps({"stored": True, "bucket": bucket, "key": key}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
