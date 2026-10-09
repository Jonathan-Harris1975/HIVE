"""Monthly scheduler must resolve the prior completed UTC month correctly."""
from datetime import UTC, datetime, timedelta, timezone

from app.monthly_governance_worker import previous_completed_utc_month


def test_january_rolls_back_to_december_previous_year():
    assert previous_completed_utc_month(datetime(2027, 1, 1, tzinfo=UTC)) == "2026-12"


def test_first_day_of_month_resolves_previous_month():
    assert previous_completed_utc_month(datetime(2026, 10, 1, tzinfo=UTC)) == "2026-09"


def test_last_day_of_month_still_resolves_previous_month():
    assert previous_completed_utc_month(datetime(2026, 10, 31, tzinfo=UTC)) == "2026-09"


def test_offset_timezone_is_normalised_to_utc():
    instant = datetime(2026, 10, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))
    assert previous_completed_utc_month(instant) == "2026-08"
