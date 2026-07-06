from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.rate_limit import AuthRateLimiter
from app.main import create_app


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
