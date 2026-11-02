from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Customer, Line, PanelLine, Platform, Reseller, Service


def _customer(name: str = "Mario Rossi") -> Customer:
    return Customer(name=name, search_key=name.lower())


def _line(customer: Customer, label: str | None = None) -> Line:
    return Line(
        customer=customer,
        label=label,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2026, 11, 1),
        expires_on=date(2026, 12, 1),
        anchor_day=1,
    )


async def test_reseller_name_is_unique(session) -> None:
    session.add(Reseller(name="Mario", name_key="mario"))
    await session.flush()

    session.add(Reseller(name="MARIO", name_key="mario"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_line_label_unique_per_customer(session) -> None:
    customer = _customer()
    session.add_all([customer, _line(customer, "Linea 1")])
    await session.flush()

    session.add(_line(customer, "Linea 1"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_line_label_reusable_after_soft_delete(session) -> None:
    customer = _customer()
    first = _line(customer, "Linea 1")
    first.deleted_at = datetime.now(UTC)
    session.add(first)
    await session.flush()

    session.add(_line(customer, "Linea 1"))
    await session.flush()


async def test_panel_line_platform_username_unique(session) -> None:
    customer = _customer()
    line = _line(customer)
    session.add_all([customer, line])
    await session.flush()

    session.add(
        PanelLine(
            line=line,
            platform=Platform.LIVE_8TVPLUS,
            username="mario",
            username_key="mario",
        )
    )
    await session.flush()

    other = _line(customer, "Linea 2")
    session.add(other)
    await session.flush()
    session.add(
        PanelLine(
            line=other,
            platform=Platform.LIVE_8TVPLUS,
            username="MARIO",
            username_key="mario",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_panel_line_one_per_platform_per_line(session) -> None:
    customer = _customer()
    line = _line(customer)
    session.add_all([customer, line])
    await session.flush()

    session.add(
        PanelLine(
            line=line,
            platform=Platform.LIVE_8TVPLUS,
            username="mario",
            username_key="mario",
        )
    )
    await session.flush()

    session.add(
        PanelLine(
            line=line,
            platform=Platform.LIVE_8TVPLUS,
            username="mario2",
            username_key="mario2",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_customer_cannot_be_deleted_with_lines(session) -> None:
    customer = _customer()
    session.add_all([customer, _line(customer)])
    await session.flush()

    await session.delete(customer)
    with pytest.raises(IntegrityError):
        await session.flush()
