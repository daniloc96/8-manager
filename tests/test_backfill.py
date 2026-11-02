from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models import AuditLog, Customer, Line, Reseller, Service
from app.services.backfill import (
    BackfillError,
    apply_backfill,
    ensure_reseller,
    parse_rows,
    read_rows,
    rename_customer,
)
from app.services.text import customer_search_key


def make_line(customer: Customer, price: int = 0) -> Line:
    return Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=price,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
    )


async def _seed(session):
    customer = Customer(name="Angela Mario", search_key=customer_search_key("Angela Mario"))
    other = Customer(name="Flavia Palazzo", search_key=customer_search_key("Flavia Palazzo"))
    session.add_all([customer, other])
    await session.flush()
    lines = [make_line(customer), make_line(other)]
    session.add_all(lines)
    await session.flush()
    return customer, other, lines


def test_parse_rows_accepts_semicolon_and_comma() -> None:
    rows = parse_rows("1;Mario;7\n2;-;5,50\n\n")
    assert [(row.line_id, row.reseller, row.price_cents) for row in rows] == [
        (1, "Mario", 700),
        (2, "-", 550),
    ]

    comma_rows = parse_rows("linea,rivenditore,prezzo\n3,Mario,6\n")
    assert [(row.line_id, row.price_cents) for row in comma_rows] == [(3, 600)]


def test_parse_rows_ignores_comments_and_blanks() -> None:
    rows = parse_rows("linea;rivenditore;prezzo\n# nota\n1;Mario;7\n\n")
    assert [(row.line_id, row.price_cents) for row in rows] == [(1, 700)]


def test_parse_rows_rejects_bad_values() -> None:
    with pytest.raises(BackfillError):
        parse_rows("x;Mario;7")
    with pytest.raises(BackfillError):
        parse_rows("1;Mario;molto")
    with pytest.raises(BackfillError):
        parse_rows("1;Mario")


def test_read_rows_from_file(tmp_path) -> None:
    path = tmp_path / "backfill.csv"
    path.write_text("1;Mario;7\n2;-;5\n", encoding="utf-8")
    assert len(read_rows(path)) == 2


async def test_apply_backfill_dry_run_does_not_write(session) -> None:
    _customer, _other, lines = await _seed(session)
    rows = parse_rows(f"{lines[0].id};Mario;7\n{lines[1].id};-;5\n")

    report = await apply_backfill(session, rows, apply=False)

    assert len(report.changes) == 2
    assert report.changes[0].new_reseller == "Mario"
    assert lines[0].monthly_price_cents == 0
    assert await session.scalar(select(Reseller)) is None


async def test_apply_backfill_writes_price_and_reseller(session) -> None:
    _customer, _other, lines = await _seed(session)
    rows = parse_rows(f"{lines[0].id};Mario;7\n{lines[1].id};-;10\n")

    report = await apply_backfill(session, rows, apply=True)

    assert report.applied is True
    mario = await session.scalar(select(Reseller).where(Reseller.name_key == "mario"))
    assert mario is not None
    assert lines[0].reseller_id == mario.id
    assert lines[0].monthly_price_cents == 700
    assert lines[1].reseller_id is None
    assert lines[1].monthly_price_cents == 1000

    audit = list((await session.scalars(select(AuditLog))).all())
    assert len(audit) == 2
    assert audit[0].action == "backfill_commercial"


async def test_apply_backfill_reports_unknown_and_duplicate(session) -> None:
    _customer, _other, lines = await _seed(session)
    rows = parse_rows(f"{lines[0].id};Mario;7\n{lines[0].id};Mario;8\n9999;Mario;7\n")

    report = await apply_backfill(session, rows, apply=False)

    assert report.unknown_lines == [9999]
    assert any("più di una volta" in error for error in report.errors)
    assert len(report.changes) == 1


async def test_apply_backfill_ignores_disabled_lines(session) -> None:
    _customer, _other, lines = await _seed(session)
    from datetime import UTC, datetime

    lines[0].deleted_at = datetime.now(UTC)
    await session.flush()

    report = await apply_backfill(session, parse_rows(f"{lines[0].id};Mario;7"), apply=True)

    assert report.unknown_lines == [lines[0].id]
    assert lines[0].monthly_price_cents == 0


async def test_ensure_reseller_creates_and_links(session) -> None:
    record = await ensure_reseller(session, name="Mario", telegram_user_id=1234)
    assert record.name_key == "mario"
    assert record.telegram_user_id == 1234
    assert record.active is True

    updated = await ensure_reseller(
        session, name="Mario B.", telegram_user_id=1234, default_price_cents=650
    )
    assert updated.id == record.id
    assert updated.name == "Mario B."
    assert updated.default_monthly_price_cents == 650


async def test_ensure_reseller_reuses_name_without_id(session) -> None:
    created = await ensure_reseller(session, name="Mario")
    linked = await ensure_reseller(session, name="mario", telegram_user_id=99)
    assert linked.id == created.id
    assert linked.telegram_user_id == 99


async def test_ensure_reseller_rejects_telegram_id_clash(session) -> None:
    await ensure_reseller(session, name="Mario", telegram_user_id=1234)
    await ensure_reseller(session, name="Sara", telegram_user_id=5678)

    with pytest.raises(BackfillError):
        await ensure_reseller(session, name="Mario", telegram_user_id=5678)


async def test_rename_customer_updates_search_key(session) -> None:
    _customer, _other, lines = await _seed(session)

    renamed = await rename_customer(session, line_id=lines[0].id, name="Angela Rossi")

    assert renamed.name == "Angela Rossi"
    assert renamed.search_key.startswith("angela rossi")
    audit = await session.scalar(select(AuditLog))
    assert audit.action == "customer_renamed"
