#!/usr/bin/env python3
"""Durable R2-backed mutation lease for Kilo/cto.new collision fencing."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _key_for(repository: str, fingerprint: str) -> str:
    digest = hashlib.sha256(f"{repository}\n{fingerprint}".encode()).hexdigest()
    return f"ci-state/{repository.replace('/', '__')}/mutation-leases/{digest}.json"


def _signing_key(secret: str, date: str) -> bytes:
    k_date = hmac.new(("AWS4" + secret).encode(), date.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, b"auto", hashlib.sha256).digest()
    k_service = hmac.new(k_region, b"s3", hashlib.sha256).digest()
    return hmac.new(k_service, b"aws4_request", hashlib.sha256).digest()


def _request(method: str, bucket: str, key: str, body: bytes = b"", extra: dict[str, str] | None = None):
    account = _required("R2_ACCOUNT_ID")
    access = _required("R2_ACCESS_KEY_ID")
    secret = _required("R2_SECRET_ACCESS_KEY")
    host = f"{account}.r2.cloudflarestorage.com"
    path = "/" + "/".join(urllib.parse.quote(part, safe="-._~") for part in [bucket, *key.split("/")])
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    short = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if body:
        headers["content-type"] = "application/json"
    for name, value in (extra or {}).items():
        headers[name.lower()] = value
    names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name].strip()}\n" for name in names)
    signed_headers = ";".join(names)
    canonical = "\n".join([method, path, "", canonical_headers, signed_headers, payload_hash])
    scope = f"{short}/auto/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()
    ])
    signature = hmac.new(_signing_key(secret, short), string_to_sign.encode(), hashlib.sha256).hexdigest()
    request_headers = {k: v for k, v in headers.items() if k != "host"}
    request_headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return urllib.request.Request(
        f"https://{host}{path}",
        data=body if method == "PUT" else None,
        headers=request_headers,
        method=method,
    )


def _read(bucket: str, key: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        with urllib.request.urlopen(_request("GET", bucket, key), timeout=30) as response:
            etag = response.headers.get("ETag")
            return json.loads(response.read().decode("utf-8")), etag
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise


def _put(bucket: str, key: str, payload: dict[str, object], condition: dict[str, str]) -> str | None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    try:
        with urllib.request.urlopen(_request("PUT", bucket, key, body, condition), timeout=30) as response:
            return response.headers.get("ETag")
    except urllib.error.HTTPError as exc:
        if exc.code == 412:
            return None
        raise


def acquire(repository: str, fingerprint: str, owner: str, ttl_seconds: int, bucket: str) -> dict[str, object]:
    if owner not in {"kilo", "cto.new"}:
        raise RuntimeError("owner must be kilo or cto.new")
    if not 60 <= ttl_seconds <= 7200:
        raise RuntimeError("lease ttl must be 60..7200 seconds")

    key = _key_for(repository, fingerprint)
    current, etag = _read(bucket, key)
    now = dt.datetime.now(dt.timezone.utc)
    generation = 1

    if current:
        expires = dt.datetime.fromisoformat(str(current["expires_at"]).replace("Z", "+00:00"))
        if expires > now:
            raise RuntimeError(
                f"fingerprint already leased by {current.get('owner')} until {current.get('expires_at')}"
            )
        generation = int(current.get("generation", 0)) + 1

    token = secrets.token_urlsafe(24)
    expires = now + dt.timedelta(seconds=ttl_seconds)
    payload: dict[str, object] = {
        "schema_version": 1,
        "repository": repository,
        "fingerprint_sha256": hashlib.sha256(fingerprint.encode()).hexdigest(),
        "owner": owner,
        "generation": generation,
        "token": token,
        "acquired_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
    }
    condition = {"if-none-match": "*"} if etag is None else {"if-match": etag}
    new_etag = _put(bucket, key, payload, condition)
    if new_etag is None:
        raise RuntimeError("lease acquisition lost a concurrent race; no mutation authority granted")
    payload["key"] = key
    return payload


def release(repository: str, fingerprint: str, owner: str, token: str, bucket: str) -> dict[str, object]:
    key = _key_for(repository, fingerprint)
    current, etag = _read(bucket, key)
    if not current or not etag:
        raise RuntimeError("lease does not exist")
    if current.get("owner") != owner or current.get("token") != token:
        raise RuntimeError("lease owner/token mismatch")
    now = dt.datetime.now(dt.timezone.utc)
    payload = dict(current)
    payload["released_at"] = now.isoformat().replace("+00:00", "Z")
    payload["expires_at"] = payload["released_at"]
    new_etag = _put(bucket, key, payload, {"if-match": etag})
    if new_etag is None:
        raise RuntimeError("lease release lost a concurrent race")
    payload["key"] = key
    return payload



def renew(repository: str, fingerprint: str, owner: str, token: str, ttl_seconds: int, bucket: str) -> dict[str, object]:
    """Extend an unexpired lease using its ETag as a compare-and-swap guard."""
    if owner not in {"kilo", "cto.new"}:
        raise RuntimeError("owner must be kilo or cto.new")
    if not 60 <= ttl_seconds <= 7200:
        raise RuntimeError("lease ttl must be 60..7200 seconds")
    key = _key_for(repository, fingerprint)
    current, etag = _read(bucket, key)
    if not current or not etag:
        raise RuntimeError("lease does not exist")
    if current.get("owner") != owner or not hmac.compare_digest(str(current.get("token", "")), token):
        raise RuntimeError("lease owner/token mismatch")
    if current.get("repository") != repository or current.get("fingerprint_sha256") != hashlib.sha256(fingerprint.encode()).hexdigest():
        raise RuntimeError("lease identity mismatch")
    now = dt.datetime.now(dt.timezone.utc)
    expires = dt.datetime.fromisoformat(str(current["expires_at"]).replace("Z", "+00:00"))
    if expires <= now:
        raise RuntimeError("expired lease cannot be renewed")
    payload = dict(current)
    payload["expires_at"] = (now + dt.timedelta(seconds=ttl_seconds)).isoformat().replace("+00:00", "Z")
    new_etag = _put(bucket, key, payload, {"if-match": etag})
    if new_etag is None:
        raise RuntimeError("lease renewal lost a concurrent race")
    payload["key"] = key
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["acquire", "renew", "release"])
    parser.add_argument("--repository", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--token")
    parser.add_argument("--ttl-seconds", type=int, default=1800)
    parser.add_argument("--bucket", default=os.environ.get("R2_BUCKET", "hive-repositories"))
    args = parser.parse_args()

    if args.command == "acquire":
        result = acquire(args.repository, args.fingerprint, args.owner, args.ttl_seconds, args.bucket)
    else:
        if not args.token:
            raise RuntimeError("--token is required for renewal or release")
        if args.command == "renew":
            result = renew(args.repository, args.fingerprint, args.owner, args.token, args.ttl_seconds, args.bucket)
        else:
            result = release(args.repository, args.fingerprint, args.owner, args.token, args.bucket)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
