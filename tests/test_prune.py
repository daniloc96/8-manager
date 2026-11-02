from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.models import (
    Customer,
    Line,
    PanelLine,
    Platform,
    Service,
    SyncRun,
    SyncStatus,
)
from app.services.prune import MAX_MISSING_RATIO, PruneBlocked, prune_missing

NOW = datetime(2026, 11, 2, 20, 30, tzinfo=UTC)
OLDER = datetime(2026, 11, 2, 19, 0, tzinfo=UTC)


def panel(
    platform: Platform,
    username: str,
    expires_on: date,
    seen_at: datetime,
) -> PanelLine:
    return PanelLine(
        platform=platform,
        username=username,
        username_key=username,
        expires_on=expires_on,
        first_seen_at=OLDER,
        last_seen_at=seen_at,
    )


def run(platform: Platform, started_at: datetime = NOW) -> SyncRun:
    return SyncRun(
        platform=platform,
        started_at=started_at,
        finished_at=started_at,
        status=SyncStatus.SUCCESS,
        counts={"fetched": 0},
    )


def line(customer_id: int, *, expires_on: date, service=Service.LIVE_VOD) -> Line:
    return Line(
        customer_id=customer_id,
        service=service,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=expires_on,
        anchor_day=1,
    )


async def _seed(session, rows):
    session.add_all(rows)
    await session.flush()


async def test_prune_closes_expired_line_gone_from_both_panels(session) -> None:
    customer = Customer(name="Mario Rossi", search_key="mario rossi")
    session.add(customer)
    await session.flush()
    target = line(customer.id, expires_on=date(2026, 8, 1))
    session.add(target)
    await session.flush()
    session.add_all(
        [
            run(Platform.LIVE_HALLO),
            run(Platform.VOD_NEWVOD),
            panel(Platform.LIVE_HALLO, "mario", date(2026, 8, 1), OLDER),
            panel(Platform.VOD_NEWVOD, "mario", date(2026, 8, 1), OLDER),
        ]
    )
    await session.flush()
    for record in (await session.scalars(select(PanelLine))).all():
        record.line_id = target.id
    await session.flush()

    report = await prune_missing(session, today=date(2026, 11, 2), apply=True, force=True)

    assert [item[0] for item in report.closed] == [target.id]
    assert await session.scalar(select(func.count(PanelLine.id))) == 0
    assert target.deleted_at is not None


async def test_prune_keeps_line_that_is_still_on_one_panel(session) -> None:
    customer = Customer(name="Anna Bianchi", search_key="anna bianchi")
    session.add(customer)
    await session.flush()
    target = line(customer.id, expires_on=date(2026, 8, 1))
    session.add(target)
    await session.flush()
    live = panel(Platform.LIVE_HALLO, "anna", date(2026, 8, 1), OLDER)
    vod = panel(Platform.VOD_NEWVOD, "anna", date(2027, 2, 1), NOW)
    session.add_all([run(Platform.LIVE_HALLO), run(Platform.VOD_NEWVOD), live, vod])
    await session.flush()
    live.line_id = target.id
    vod.line_id = target.id
    await session.flush()

    report = await prune_missing(session, today=date(2026, 11, 2), apply=True, force=True)

    assert report.closed == []
    assert [item[0] for item in report.undecided] == [target.id]
    assert target.deleted_at is None
    assert await session.scalar(select(func.count(PanelLine.id))) == 2


async def test_prune_closes_missing_line_even_if_not_expired(session) -> None:
    customer = Customer(name="Carlo Verdi", search_key="carlo verdi")
    session.add(customer)
    await session.flush()
    target = line(customer.id, expires_on=date(2027, 3, 1))
    session.add(target)
    await session.flush()
    record = panel(Platform.LIVE_HALLO, "carlo", date(2027, 3, 1), OLDER)
    session.add_all([run(Platform.LIVE_HALLO), record])
    await session.flush()
    record.line_id = target.id
    await session.flush()

    report = await prune_missing(session, today=date(2026, 11, 2), apply=True, force=True)

    # la cancellazione remota è la fonte di verità: si chiude anche se la
    # scadenza locale è ancora nel futuro
    assert [item[0] for item in report.closed] == [target.id]
    assert report.undecided == []
    assert target.deleted_at is not None


async def test_prune_deletes_orphan_rows_and_dry_run_writes_nothing(session) -> None:
    session.add_all(
        [
            run(Platform.VOD_NEWVOD),
            panel(Platform.VOD_NEWVOD, "vecchio", date(2023, 1, 5), OLDER),
            panel(Platform.VOD_NEWVOD, "attivo", date(2027, 1, 5), NOW),
        ]
    )
    await session.flush()

    report = await prune_missing(session, today=date(2026, 11, 2), apply=False)
    assert report.orphan_rows == 1
    assert await session.scalar(select(func.count(PanelLine.id))) == 2

    report = await prune_missing(session, today=date(2026, 11, 2), apply=True, force=True)
    assert report.orphan_rows == 1
    remaining = (await session.scalars(select(PanelLine.username))).all()
    assert remaining == ["attivo"]


async def test_prune_blocks_when_too_many_rows_disappear(session) -> None:
    session.add(run(Platform.LIVE_HALLO))
    for index in range(10):
        seen_at = OLDER if index < 4 else NOW
        session.add(panel(Platform.LIVE_HALLO, f"utente{index}", date(2026, 8, 1), seen_at))
    await session.flush()

    report = await prune_missing(session, today=date(2026, 11, 2), apply=False)
    assert report.ratios()[Platform.LIVE_HALLO] == pytest.approx(0.4)
    assert report.over_limit() == [Platform.LIVE_HALLO]
    assert MAX_MISSING_RATIO < 0.4

    with pytest.raises(PruneBlocked):
        await prune_missing(session, today=date(2026, 11, 2), apply=True)

    report = await prune_missing(session, today=date(2026, 11, 2), apply=True, force=True)
    assert report.orphan_rows == 4
    assert await session.scalar(select(func.count(PanelLine.id))) == 6
