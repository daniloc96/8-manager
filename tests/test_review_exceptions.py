from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    Customer,
    Line,
    Platform,
    ReviewException,
    Service,
)
from app.panels.base import PanelLine as PanelRow
from app.panels.sync import run_sync
from app.services.review_exceptions import (
    ReviewExceptionError,
    clear_exception,
    set_exception,
)


class FakeAdapter:
    def __init__(self, platform: Platform, rows: list[PanelRow]) -> None:
        self.platform = platform
        self._rows = rows

    async def fetch_lines(self) -> list[PanelRow]:
        return list(self._rows)

    async def aclose(self) -> None:
        return None


def row(platform: Platform, username: str, expires_on: date, note: str = "Mario Rossi"):
    return PanelRow(
        platform=platform,
        external_id=f"{platform.value}-{username}",
        username=username,
        password="password",
        access_code=username,
        expires_on=expires_on,
        status="active",
        note=note,
        max_connections=1,
        raw={},
    )


def make_line(expires_on: date = date(2026, 8, 1)) -> tuple[Customer, Line]:
    customer = Customer(name="Mario Rossi", search_key="mario rossi")
    line = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=expires_on,
        anchor_day=1,
    )
    return customer, line


async def test_set_is_listed_and_removed(session) -> None:
    customer, line = make_line()
    session.add_all([customer, line])
    await session.flush()

    record = await set_exception(
        session, line_id=line.id, reason="linea personale", review_on=date(2027, 1, 4)
    )
    assert record.review_on == date(2027, 1, 4)

    stored = await session.scalar(select(ReviewException))
    assert stored.reason == "linea personale"

    await set_exception(session, line_id=line.id, reason="cambiato motivo", review_on=None)
    stored = await session.scalar(select(ReviewException))
    assert stored.reason == "cambiato motivo"
    assert stored.review_on is None

    await clear_exception(session, line_id=line.id)
    assert await session.scalar(select(ReviewException)) is None


async def test_exception_requires_line_and_reason(session) -> None:
    customer, line = make_line()
    session.add_all([customer, line])
    await session.flush()

    try:
        await set_exception(session, line_id=line.id, reason="   ")
    except ReviewExceptionError as exc:
        assert "motivo" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("doveva rifiutare un motivo vuoto")

    try:
        await set_exception(session, line_id=9999, reason="x")
    except ReviewExceptionError as exc:
        assert "non trovata" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("doveva rifiutare una linea inesistente")


async def test_sync_hides_exception_until_review_date(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = FakeAdapter(
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", date(2026, 8, 1))],
    )
    await run_sync([first], today=date(2026, 11, 1), import_missing=True, session_factory=factory)

    second = FakeAdapter(Platform.LIVE_HALLO, [])
    report = await run_sync([second], today=date(2026, 11, 2), session_factory=factory)
    assert report.review_count == 1
    assert report.suppressed == 0

    async with factory() as session:
        line = await session.scalar(select(Line))
        await set_exception(
            session,
            line_id=line.id,
            reason="sparita ma cliente ancora attivo",
            review_on=date(2026, 12, 19),
        )
        await session.commit()

    report = await run_sync([second], today=date(2026, 11, 2), session_factory=factory)
    assert report.review_count == 0
    assert report.suppressed == 1
    assert report.due_exceptions == []

    report = await run_sync([second], today=date(2026, 12, 19), session_factory=factory)
    assert report.review_count == 1
    assert report.suppressed == 0
    assert report.due_exceptions == [(1, "sparita ma cliente ancora attivo", "2026-12-19")]


async def test_sync_keeps_permanent_exception_hidden(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = FakeAdapter(
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", date(2026, 8, 1))],
    )
    await run_sync([first], today=date(2026, 11, 1), import_missing=True, session_factory=factory)

    async with factory() as session:
        line = await session.scalar(select(Line))
        await set_exception(session, line_id=line.id, reason="linea personale")
        await session.commit()

    second = FakeAdapter(Platform.LIVE_HALLO, [])
    report = await run_sync([second], today=date(2030, 1, 1), session_factory=factory)

    assert report.review_count == 0
    assert report.suppressed == 1
    assert report.due_exceptions == []
