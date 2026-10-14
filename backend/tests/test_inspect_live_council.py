from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path
from urllib.request import Request


SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "inspect_live_council.py"
SPEC = importlib.util.spec_from_file_location("inspect_live_council", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_redirect_handler_refuses_redirect_with_authorization_header():
    request = Request(
        "https://hive.example/v1/ai-council/status",
        headers={"Authorization": "Bearer secret"},
    )

    redirected = module.NoRedirectHandler().redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://attacker.example/collect",
    )

    assert redirected is None


def test_council_summary_reports_http_status_without_response_body():
    status = {
        "ok": False,
        "fresh": True,
        "period": "2026-10",
        "completion_status": "degraded",
        "downstream_sync_enabled": True,
        "downstream_sync_ok": False,
        "reason": "latest AI Council run is degraded",
    }
    history = {
        "runs": [
            {
                "run_id": "run-1",
                "completion_status": "degraded",
                "completed_at": "2026-10-01T08:00:00Z",
                "downstream_sync": {
                    "enabled": True,
                    "ok": False,
                    "error": "HTTP 401: confidential response body",
                },
            }
        ]
    }

    output = "\n".join(module.summarize(status, history))

    assert "downstream_http_status=401" in output
    assert "confidential response body" not in output
    assert "run_id=run-1" in output


def test_council_summary_reports_per_target_verification():
    status = {"ok": True, "fresh": True, "period": "2026-10"}
    history = {
        "runs": [
            {
                "run_id": "run-2",
                "completion_status": "completed",
                "downstream_sync": {
                    "enabled": True,
                    "ok": True,
                    "targets": {
                        "aims": {"ok": True, "http_status": 200, "attempt": 1},
                        "rams": {"ok": True, "http_status": 200, "attempt": 2},
                    },
                },
            }
        ]
    }

    output = "\n".join(module.summarize(status, history))

    assert "aims_ok=True aims_http_status=200 aims_attempt=1" in output
    assert "rams_ok=True rams_http_status=200 rams_attempt=2" in output


def test_council_summary_classifies_premium_approval_failure_without_leaking_body():
    status = {"ok": False, "completion_status": "degraded"}
    history = {
        "runs": [
            {
                "run_id": "run-3",
                "completion_status": "degraded",
                "downstream_sync": {
                    "enabled": True,
                    "ok": False,
                    "error": (
                        'HTTP 422: {"error":"premium_justification missing",'
                        '"model":"private-model-name","detail":"private response"}'
                    ),
                },
            }
        ]
    }

    output = "\\n".join(module.summarize(status, history))

    assert "downstream_http_status=422" in output
    assert "downstream_failure_category=premium_approval" in output
    assert "premium_justification" not in output
    assert "private-model-name" not in output
    assert "private response" not in output


def test_council_summary_classifies_generic_422_as_validation_without_leaking_body():
    status = {"ok": False, "completion_status": "degraded"}
    history = {
        "runs": [
            {
                "run_id": "run-4",
                "completion_status": "degraded",
                "downstream_sync": {
                    "enabled": True,
                    "ok": False,
                    "error": "HTTP 422: confidential validation response",
                },
            }
        ]
    }

    output = "\\n".join(module.summarize(status, history))

    assert "downstream_http_status=422" in output
    assert "downstream_failure_category=downstream_validation" in output
    assert "confidential validation response" not in output


def test_council_summary_classifies_retryable_downstream_statuses_as_unavailable():
    assert module.downstream_failure_category("HTTP 408: private", "408") == "downstream_unavailable"
    assert module.downstream_failure_category("HTTP 409: private", "409") == "downstream_unavailable"
    assert module.downstream_failure_category("HTTP 425: private", "425") == "downstream_unavailable"
    assert module.downstream_failure_category("HTTP 422: private", "422") == "downstream_validation"
    assert module.downstream_failure_category("HTTP 422: approved by generic CI", "422") == "downstream_validation"



def test_get_json_retries_transient_gateway_timeout(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            if len(calls) == 1:
                raise urllib.error.HTTPError(request.full_url, 504, "gateway timeout", {}, None)
            return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    assert module.get_json("https://hive.example", "secret", "/status") == {"ok": True}
    assert len(calls) == 2
    assert calls[0][0].get_header("Authorization") == "Bearer secret"


def test_get_json_retries_transient_connection_error(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.URLError("temporary connection reset")
            return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    assert module.get_json("https://hive.example", "secret", "/status") == {"ok": True}
    assert len(calls) == 2


def test_get_json_does_not_retry_authentication_failure(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, 401, "unauthorised", {}, None)

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    try:
        module.get_json("https://hive.example", "secret", "/status")
    except RuntimeError as exc:
        assert str(exc) == "GET /status failed with HTTP 401"
    else:
        raise AssertionError("authentication failure should fail immediately")
    assert len(calls) == 1



def test_get_json_retries_invalid_transient_response(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return io.BytesIO(b'not-json')
            return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)

    assert module.get_json("https://hive.example", "secret", "/status") == {"ok": True}
    assert len(calls) == 2



def test_get_json_obeys_retry_after_seconds(monkeypatch):
    calls = []
    sleeps = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate limited",
                    {"Retry-After": "7"},
                    None,
                )
            return io.BytesIO(b'{"ok": true}')

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    assert module.get_json("https://hive.example", "secret", "/status") == {"ok": True}
    assert len(calls) == 2
    assert sleeps == [7.0]


def test_get_json_does_not_retry_before_an_excessive_retry_after(monkeypatch):
    calls = []
    sleeps = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "60"},
                None,
            )

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *_: Opener())
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    try:
        module.get_json("https://hive.example", "secret", "/status")
    except RuntimeError as exc:
        assert str(exc) == "GET /status failed with HTTP 429"
    else:
        raise AssertionError("rate limit beyond the retry budget should fail closed")
    assert len(calls) == 1
    assert sleeps == []


def test_retry_delay_falls_back_for_non_finite_values():
    import math

    error = urllib.error.HTTPError(
        "https://hive.example/status",
        429,
        "rate limited",
        {"Retry-After": "NaN"},
        None,
    )

    assert module._retry_delay(error, 2) == 2.0

    for value in ("Infinity", "-Infinity"):
        error = urllib.error.HTTPError(
            "https://hive.example/status",
            429,
            "rate limited",
            {"Retry-After": value},
            None,
        )
        assert math.isfinite(module._retry_delay(error, 2))
        assert module._retry_delay(error, 2) == 2.0
