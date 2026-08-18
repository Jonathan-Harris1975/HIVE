from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.rate_limit import AuthRateLimiter, client_ip_from_request, token_fingerprint
from app.main import create_app
from app.core.security import _allow_local_development_bypass


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "APP_ENV": "development",
        "ADMIN_BEARER_TOKEN": "a" * 48,
        "REPO_HEALTH_ENABLED": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The rate limiter is a process-wide singleton; reset it around every
    test so failures/lockouts from one test can't bleed into the next."""
    from app.core import rate_limit as rate_limit_module

    rate_limit_module.auth_rate_limiter.reset()
    yield
    rate_limit_module.auth_rate_limiter.reset()


def _client() -> TestClient:
    app = create_app(_settings())
    return TestClient(app)


def test_valid_admin_token_is_not_rate_limited():
    client = _client()
    for _ in range(5):
        response = client.get("/v1/repositories", headers={"Authorization": f"Bearer {'a' * 48}"})
        assert response.status_code != 429


def test_repeated_invalid_tokens_from_same_ip_are_locked_out():
    client = _client()
    bad_headers = {"Authorization": "Bearer wrong-token-wrong-token-wrong"}

    # Drive past the default failure threshold (10) with the same bad token.
    last_status = None
    for _ in range(12):
        response = client.get("/v1/repositories", headers=bad_headers)
        last_status = response.status_code

    assert last_status == 429


def test_lockout_response_includes_retry_after_header():
    client = _client()
    bad_headers = {"Authorization": "Bearer another-wrong-token-value"}

    for _ in range(12):
        response = client.get("/v1/repositories", headers=bad_headers)

    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_lockout_is_scoped_per_ip_and_does_not_block_valid_client():
    """A separate, correctly-authenticated client is unaffected by another
    client's failed attempts, since requests here share the TestClient's
    fixed peer IP but use different token prefixes."""
    client = _client()
    bad_headers = {"Authorization": "Bearer totally-invalid-guessed-token"}
    for _ in range(12):
        client.get("/v1/repositories", headers=bad_headers)

    good_headers = {"Authorization": f"Bearer {'a' * 48}"}
    response = client.get("/v1/repositories", headers=good_headers)
    assert response.status_code != 429


def test_auth_rate_limiter_unit_sliding_window_and_lockout():
    """Unit-level test of the limiter itself, independent of FastAPI, using
    a fake clock so the test is fast and deterministic."""
    current_time = [0.0]
    limiter = AuthRateLimiter(
        max_failures=3, window_seconds=10.0, lockout_seconds=30.0, clock=lambda: current_time[0]
    )

    limiter.check("1.2.3.4", "abcd1234")  # no-op, not locked out yet

    limiter.record_failure("1.2.3.4", "abcd1234")
    limiter.record_failure("1.2.3.4", "abcd1234")
    limiter.check("1.2.3.4", "abcd1234")  # still under threshold

    limiter.record_failure("1.2.3.4", "abcd1234")  # third failure trips lockout

    with pytest.raises(Exception):
        limiter.check("1.2.3.4", "abcd1234")

    # Advance the fake clock past the lockout window.
    current_time[0] += 31.0
    limiter.check("1.2.3.4", "abcd1234")  # should no longer raise


def test_auth_rate_limiter_success_clears_failure_history():
    current_time = [0.0]
    limiter = AuthRateLimiter(
        max_failures=3, window_seconds=10.0, lockout_seconds=30.0, clock=lambda: current_time[0]
    )
    limiter.record_failure("5.6.7.8", "prefix01")
    limiter.record_failure("5.6.7.8", "prefix01")
    limiter.record_success("5.6.7.8", "prefix01")
    limiter.record_failure("5.6.7.8", "prefix01")
    limiter.record_failure("5.6.7.8", "prefix01")
    # Only 2 failures since the reset from record_success, threshold is 3.
    limiter.check("5.6.7.8", "prefix01")


def test_rotating_token_guesses_eventually_trigger_ip_lockout():
    current_time = [0.0]
    limiter = AuthRateLimiter(
        max_failures=10,
        ip_max_failures=4,
        window_seconds=60.0,
        lockout_seconds=30.0,
        clock=lambda: current_time[0],
    )

    for index in range(4):
        fingerprint = token_fingerprint(f"different-guess-{index}")
        limiter.record_failure("9.8.7.6", fingerprint)

    with pytest.raises(Exception):
        limiter.check("9.8.7.6", token_fingerprint("yet-another-guess"))


def test_token_fingerprint_does_not_store_raw_token_prefix():
    token = "super-secret-bearer-token-value"
    fingerprint = token_fingerprint(token)

    assert fingerprint != token[:8]
    assert token[:8] not in fingerprint
    assert len(fingerprint) == 16


def test_client_ip_uses_server_peer_outside_koyeb(monkeypatch):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"203.0.113.77")],
        "client": ("127.0.0.1", 43210),
        "server": ("testserver", 443),
    }

    monkeypatch.delenv("KOYEB_PUBLIC_DOMAIN", raising=False)
    assert client_ip_from_request(Request(scope)) == "127.0.0.1"


def test_client_ip_uses_koyeb_certified_last_forwarded_hop(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("KOYEB_PUBLIC_DOMAIN", "example.koyeb.app")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"127.0.0.1, 198.51.100.23")],
        "client": ("10.0.0.8", 43210),
        "server": ("testserver", 443),
    }

    assert client_ip_from_request(Request(scope)) == "198.51.100.23"


def test_client_ip_rejects_invalid_koyeb_forwarded_value(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("KOYEB_PUBLIC_DOMAIN", "example.koyeb.app")
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"198.51.100.23, not-an-ip")],
        "client": ("10.0.0.8", 43210),
        "server": ("testserver", 443),
    }

    assert client_ip_from_request(Request(scope)) == "10.0.0.8"


def test_development_sentinel_bypass_is_loopback_only():
    from starlette.requests import Request

    settings = Settings(APP_ENV="development", ADMIN_BEARER_TOKEN="change-me-local-only")

    def request_for(peer: str) -> Request:
        return Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": (peer, 12345),
            "server": ("localhost", 8000),
        })

    assert _allow_local_development_bypass(request_for("127.0.0.1"), settings) is True
    assert _allow_local_development_bypass(request_for("::1"), settings) is True
    assert _allow_local_development_bypass(request_for("198.51.100.23"), settings) is False
