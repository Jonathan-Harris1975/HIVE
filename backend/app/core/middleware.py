from __future__ import annotations

import json
import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings

logger = logging.getLogger("uvicorn.error.hive.request")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class RequestBodyTooLarge(Exception):
    pass


class ProductionMiddleware:
    """Pure ASGI middleware for request IDs, body limits, headers, and safe logs.

    It deliberately avoids BaseHTTPMiddleware so server-sent event responses remain
    streamed rather than buffered.
    """

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings
        self.max_body_bytes = max(1, int(settings.max_request_body_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _request_id(headers.get("x-request-id"))
        scope.setdefault("state", {})["request_id"] = request_id
        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "/")
        started_at = time.perf_counter()
        status_code = 500
        response_started = False
        bytes_received = 0

        content_length = _parse_content_length(headers.get("content-length"))
        if content_length is not None and content_length > self.max_body_bytes:
            await self._send_too_large(send, request_id)
            self._log(method, path, 413, started_at, request_id)
            return

        async def limited_receive() -> Message:
            nonlocal bytes_received
            message = await receive()
            if message.get("type") == "http.request":
                bytes_received += len(message.get("body", b""))
                if bytes_received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started
            if message.get("type") == "http.response.start":
                response_started = True
                status_code = int(message.get("status") or 500)
                mutable = MutableHeaders(scope=message)
                mutable["X-Request-ID"] = request_id
                if self.settings.security_headers_enabled:
                    _apply_security_headers(mutable, path=path, production=not self.settings.is_dev)
            await send(message)

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except RequestBodyTooLarge:
            status_code = 413
            if not response_started:
                await self._send_too_large(send, request_id)
        finally:
            self._log(method, path, status_code, started_at, request_id)

    async def _send_too_large(self, send: Send, request_id: str) -> None:
        payload = json.dumps(
            {
                "detail": "Request body exceeds the configured limit",
                "request_id": request_id,
                "max_bytes": self.max_body_bytes,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(payload)).encode("ascii")),
            (b"x-request-id", request_id.encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"connection", b"close"),
        ]
        await send({"type": "http.response.start", "status": 413, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    def _log(self, method: str, path: str, status_code: int, started_at: float, request_id: str) -> None:
        if not self.settings.request_logging_enabled:
            return
        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        logger.info(
            "request_complete request_id=%s method=%s path=%s status=%s duration_ms=%s",
            request_id,
            method,
            path,
            status_code,
            duration_ms,
        )


def _request_id(value: str | None) -> str:
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _apply_security_headers(headers: MutableHeaders, *, path: str, production: bool) -> None:
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
    if path.startswith("/v1") or path in {"/health", "/healthz", "/livez", "/readyz"}:
        headers.setdefault("Cache-Control", "no-store")
        headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
    if production:
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
