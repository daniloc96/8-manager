from __future__ import annotations

from datetime import date

import pytest

from app.models.enums import LineStatus
from app.services.months import (
    add_months,
    days_left,
    months_between,
    plan_renewal,
    status_for,
)


def test_add_months_calendar() -> None:
    assert add_months(date(2026, 11, 13), 2) == date(2027, 1, 13)
    assert add_months(date(2026, 12, 31), 1) == date(2027, 1, 31)
    assert add_months(date(2026, 1, 15), 12) == date(2027, 1, 15)


def test_add_months_end_of_month_clamps() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_add_months_anchor_prevents_drift() -> None:
    february = add_months(date(2026, 1, 31), 1, anchor_day=31)
    assert february == date(2026, 2, 28)
    assert add_months(february, 1, anchor_day=31) == date(2026, 3, 31)


def test_add_months_zero_keeps_day() -> None:
    assert add_months(date(2026, 5, 20), 0) == date(2026, 5, 20)


def test_add_months_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        add_months(date(2026, 5, 20), -1)
    with pytest.raises(ValueError):
        add_months(date(2026, 5, 20), 1, anchor_day=0)
    with pytest.raises(ValueError):
        add_months(date(2026, 5, 20), 1, anchor_day=32)


def test_plan_renewal_active_adds_to_current_expiry() -> None:
    plan = plan_renewal(
        expires_on=date(2026, 11, 13),
        anchor_day=13,
        today=date(2026, 11, 1),
        months=2,
    )
    assert plan.period_start == date(2026, 11, 13)
    assert plan.period_end == date(2027, 1, 13)
    assert plan.anchor_day == 13
    assert plan.continuation is True


def test_plan_renewal_expired_restarts_today() -> None:
    plan = plan_renewal(
        expires_on=date(2026, 10, 13),
        anchor_day=13,
        today=date(2026, 11, 1),
        months=1,
    )
    assert plan.period_start == date(2026, 11, 1)
    assert plan.period_end == date(2026, 12, 1)
    assert plan.anchor_day == 1
    assert plan.continuation is False


def test_plan_renewal_on_expiry_day_is_continuation() -> None:
    plan = plan_renewal(
        expires_on=date(2026, 11, 13),
        anchor_day=13,
        today=date(2026, 11, 13),
        months=1,
    )
    assert plan.period_start == date(2026, 11, 13)
    assert plan.period_end == date(2026, 12, 13)
    assert plan.continuation is True


def test_plan_renewal_month_end() -> None:
    plan = plan_renewal(
        expires_on=date(2026, 1, 31),
        anchor_day=31,
        today=date(2026, 1, 1),
        months=1,
    )
    assert plan.period_end == date(2026, 2, 28)


def test_plan_renewal_rejects_out_of_range_months() -> None:
    with pytest.raises(ValueError):
        plan_renewal(date(2026, 1, 1), 1, date(2026, 1, 1), 0)
    with pytest.raises(ValueError):
        plan_renewal(date(2026, 1, 1), 1, date(2026, 1, 1), 37)


def test_status_and_days_left() -> None:
    today = date(2026, 11, 1)
    assert status_for(date(2026, 10, 31), today) is LineStatus.EXPIRED
    assert status_for(date(2026, 11, 1), today) is LineStatus.EXPIRING
    assert status_for(date(2026, 11, 4), today) is LineStatus.EXPIRING
    assert status_for(date(2026, 11, 5), today) is LineStatus.ACTIVE
    assert days_left(date(2026, 11, 13), today) == 12


def test_months_between_exact_jumps() -> None:
    assert months_between(date(2026, 11, 13), date(2027, 2, 13)) == 3
    assert months_between(date(2026, 1, 31), date(2026, 2, 28)) == 1
    assert months_between(date(2026, 2, 28), date(2026, 3, 31), anchor_day=31) == 1


def test_months_between_rejects_non_exact() -> None:
    assert months_between(date(2026, 1, 10), date(2026, 2, 12)) is None
    assert months_between(date(2026, 5, 1), date(2026, 4, 1)) is None
