from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.rate_limit import auth_rate_limiter
from app.main import app


_BASE_DEPENDENCY_OVERRIDES = dict(app.dependency_overrides)


@pytest.fixture(autouse=True)
def reset_process_scoped_test_state():
    """Keep process-wide auth and dependency overrides isolated per test.

    Several integration tests temporarily replace the global application's
    settings dependency. Restoring the application baseline here prevents a
    test that calls ``dependency_overrides.clear()`` from changing the auth
    mode of every test that follows it.
    """
    auth_rate_limiter.reset()
    get_settings.cache_clear()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(_BASE_DEPENDENCY_OVERRIDES)
    yield
    auth_rate_limiter.reset()
    get_settings.cache_clear()
    app.dependency_overrides.clear()
    app.dependency_overrides.update(_BASE_DEPENDENCY_OVERRIDES)
