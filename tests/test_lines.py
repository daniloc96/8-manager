from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.models import Service
from app.models.enums import RenewalSource
from app.services.lines import (
    LineDraft,
    build_customer_and_line,
    create_customer_and_line,
    renew_line,
)
from app.services.months import add_months


def test_build_customer_and_line_computes_expiry() -> None:
    today = date(2026, 11, 1)
    customer, line = build_customer_and_line(
        LineDraft(
            name="Mario Rossi",
            alias="Il Rosso",
            service=Service.LIVE_VOD,
            monthly_price_cents=500,
            months=3,
            label="Linea 1",
            start_on=date(2026, 11, 13),
        ),
        today,
    )
    assert customer.name == "Mario Rossi"
    assert customer.search_key == "mario rossi il rosso"
    assert line.expires_on == date(2027, 2, 13)
    assert line.anchor_day == 13
    assert line.service is Service.LIVE_VOD
    assert line.label == "Linea 1"


def test_build_customer_and_line_rejects_bad_input() -> None:
    today = date(2026, 11, 1)
    with pytest.raises(ValueError):
        build_customer_and_line(
            LineDraft(
                name=" ",
                service=Service.LIVE,
                monthly_price_cents=500,
                months=1,
            ),
            today,
        )
    with pytest.raises(ValueError):
        build_customer_and_line(
            LineDraft(
                name="Mario",
                service=Service.LIVE,
                monthly_price_cents=500,
                months=0,
            ),
            today,
        )


async def test_create_and_renew_expired_line(session) -> None:
    today = date(2026, 11, 1)
    _customer, line = await create_customer_and_line(
        session,
        LineDraft(
            name="Mario Rossi",
            service=Service.LIVE_VOD,
            monthly_price_cents=500,
            months=1,
            start_on=today - timedelta(days=40),
        ),
        today,
    )
    assert line.expires_on < today

    renewal = await renew_line(
        session,
        line,
        months=2,
        today=today,
        source=RenewalSource.REMINDER,
        actor_telegram_id=42,
    )

    assert line.expires_on == add_months(today, 2, today.day)
    assert line.anchor_day == today.day
    assert renewal.period_start == today
    assert renewal.period_end == line.expires_on
    assert renewal.total_cents == 1000
    assert renewal.monthly_price_cents == 500
    assert renewal.source is RenewalSource.REMINDER
    assert renewal.actor_telegram_id == 42


async def test_renew_active_line_keeps_expiry_chain(session) -> None:
    today = date(2026, 11, 1)
    _customer, line = await create_customer_and_line(
        session,
        LineDraft(
            name="Anna Bianchi",
            service=Service.LIVE,
            monthly_price_cents=400,
            months=1,
            start_on=today - timedelta(days=10),
        ),
        today,
    )
    old_expiry = line.expires_on

    renewal = await renew_line(
        session,
        line,
        months=1,
        today=today,
        source=RenewalSource.CARD,
    )

    assert renewal.period_start == old_expiry
    assert line.expires_on == add_months(old_expiry, 1, old_expiry.day)
    assert line.anchor_day == old_expiry.day


async def test_renew_with_price_override(session) -> None:
    today = date(2026, 11, 1)
    _customer, line = await create_customer_and_line(
        session,
        LineDraft(
            name="Luca Verdi",
            service=Service.LIVE,
            monthly_price_cents=500,
            months=1,
            start_on=today,
        ),
        today,
    )

    renewal = await renew_line(
        session,
        line,
        months=3,
        today=today,
        source=RenewalSource.MANUAL,
        monthly_price_cents=800,
        total_cents=2000,
    )

    assert line.monthly_price_cents == 800
    assert renewal.monthly_price_cents == 800
    assert renewal.total_cents == 2000


async def test_renew_rejects_invalid_months(session) -> None:
    today = date(2026, 11, 1)
    _customer, line = await create_customer_and_line(
        session,
        LineDraft(
            name="Luca Verdi",
            service=Service.LIVE,
            monthly_price_cents=500,
            months=1,
            start_on=today,
        ),
        today,
    )
    with pytest.raises(ValueError):
        await renew_line(session, line, months=0, today=today, source=RenewalSource.MANUAL)
