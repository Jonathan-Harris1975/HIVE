"""The worker must reject unfinished reporting periods before touching services."""
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app import monthly_governance_worker


@pytest.mark.asyncio
async def test_worker_rejects_current_month_before_loading_settings():
    current = datetime.now(UTC).strftime("%Y-%m")
    with patch.object(monthly_governance_worker, "get_settings") as settings:
        result = await monthly_governance_worker.execute(current)
    assert result == 7
    settings.assert_not_called()


@pytest.mark.asyncio
async def test_worker_rejects_future_month_before_loading_settings():
    year = datetime.now(UTC).year + 1
    with patch.object(monthly_governance_worker, "get_settings") as settings:
        result = await monthly_governance_worker.execute(f"{year}-01")
    assert result == 7
    settings.assert_not_called()
