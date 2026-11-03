from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.jobs.reminders import build_owner_reminder
from app.models import (
    Customer,
    Line,
    Notification,
    Reseller,
    ReviewException,
    Service,
)
from app.services.backup import backup_database, sqlite_path
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)


def make_line(customer: Customer, expires_on: date) -> Line:
    return Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=expires_on,
        anchor_day=1,
    )


async def _seed(factory, *, expires_on: date, name: str = "Mario Rossi") -> Line:
    async with factory() as session:
        customer = Customer(name=name, search_key=customer_search_key(name))
        session.add(customer)
        await session.flush()
        line = make_line(customer, expires_on)
        session.add(line)
        await session.commit()
        return line


async def test_reminder_is_empty_when_nothing_is_due(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory, expires_on=date(2027, 5, 1))

    assert await build_owner_reminder(TODAY, factory=factory) is None


async def test_reminder_lists_expiring_and_deduplicates(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory, expires_on=TODAY)

    first = await build_owner_reminder(TODAY, factory=factory)
    assert first is not None
    body, markup = first
    assert "Scadenze" in body
    assert "Mario Rossi" in body
    assert "scade oggi" in body
    assert any(
        "renew:" in (button.callback_data or "") for row in markup.inline_keyboard for button in row
    )

    # stessa giornata: già inviato, niente doppione
    assert await build_owner_reminder(TODAY, factory=factory) is None

    async with factory() as session:
        notifications = list((await session.scalars(select(Notification))).all())
        assert len(notifications) == 1


async def test_reminder_ignores_muted_lines(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    line = await _seed(factory, expires_on=TODAY)
    async with factory() as session:
        session.add(ReviewException(line_id=line.id, reason="linea personale"))
        await session.commit()

    assert await build_owner_reminder(TODAY, factory=factory) is None


async def test_reminder_includes_due_exception(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    line = await _seed(factory, expires_on=date(2027, 5, 1), name="Anna Bianchi")
    async with factory() as session:
        session.add(
            ReviewException(
                line_id=line.id, reason="decidere alla scadenza del VOD", review_on=TODAY
            )
        )
        await session.commit()

    payload = await build_owner_reminder(TODAY, factory=factory)
    assert payload is not None
    body, _markup = payload
    assert "Eccezioni da rivedere" in body
    assert "Anna Bianchi" not in body
    assert "decidere alla scadenza del VOD" in body


async def test_only_one_reminder_for_just_expired_line(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory, expires_on=date(2026, 11, 1))

    # il giorno dopo la scadenza: sì
    payload = await build_owner_reminder(TODAY, factory=factory)
    assert payload is not None
    assert "scaduta" in payload[0]

    # due giorni dopo: non più, per non ripetere ogni giorno
    assert await build_owner_reminder(date(2026, 11, 3), factory=factory) is None


def test_backup_database_creates_file_and_applies_retention(tmp_path: Path) -> None:
    import sqlite3

    source = tmp_path / "8manager.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("create table prova (id integer primary key)")
    connection.commit()
    connection.close()
    backup_dir = tmp_path / "backups"

    for index in range(3):
        target = backup_dir / f"8manager-20260101-00000{index}.sqlite3"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old")

    created = backup_database(source, backup_dir, retention_days=2)

    assert created.exists()
    assert created.stat().st_size > 0
    remaining = sorted(backup_dir.glob("8manager-*.sqlite3"))
    assert len(remaining) <= 2
    assert created in remaining


def test_sqlite_path_reads_url() -> None:
    assert (
        str(sqlite_path("sqlite+aiosqlite:////data/8manager.sqlite3")) == "/data/8manager.sqlite3"
    )


async def test_reseller_reminder_only_their_lines(engine) -> None:
    from app.jobs.reminders import build_reminder
    from app.models import Reseller, Service

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        reseller = Reseller(name="Luca", name_key="luca", telegram_user_id=777)
        other = Reseller(name="Sara", name_key="sara", telegram_user_id=888)
        customer = Customer(name="Mario Rossi", search_key="mario rossi")
        other_customer = Customer(name="Anna Bianchi", search_key="anna bianchi")
        session.add_all([reseller, other, customer, other_customer])
        await session.flush()
        session.add_all(
            [
                Line(
                    customer=customer,
                    service=Service.LIVE,
                    monthly_price_cents=500,
                    started_on=date(2025, 1, 1),
                    expires_on=TODAY,
                    anchor_day=1,
                    reseller_id=reseller.id,
                ),
                Line(
                    customer=other_customer,
                    service=Service.LIVE,
                    monthly_price_cents=500,
                    started_on=date(2025, 1, 1),
                    expires_on=TODAY,
                    anchor_day=1,
                    reseller_id=other.id,
                ),
            ]
        )
        await session.commit()

    payload = await build_reminder(TODAY, reseller_id=reseller.id, chat_id=777, factory=factory)

    assert payload is not None
    body, markup = payload
    assert "Mario Rossi" in body
    assert "Anna Bianchi" not in body
    assert all(
        any((button.callback_data or "").startswith(("renew:", "nr:")) for button in row)
        for row in markup.inline_keyboard[:-1]
    )
    # Ogni linea in scadenza ha il pulsante per marcarla «non rinnova».
    assert any(
        (b.callback_data or "").startswith("nr:") and (b.callback_data or "").endswith(":set")
        for row in markup.inline_keyboard
        for b in row
    )

    # L'owner non riceve le scadenze dei rivenditori: quelle sono del gruppo.
    assert await build_owner_reminder(TODAY, factory=factory) is None


async def test_owner_reminder_has_only_direct_lines(engine) -> None:
    """Le linee dei rivenditori vanno a loro: all'owner restano le dirette."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        reseller = Reseller(name="Luca", name_key="luca")
        direct = Customer(name="Diretto Uno", search_key="diretto uno")
        theirs = Customer(name="Cliente Rivenditore", search_key="cliente rivenditore")
        session.add_all([reseller, direct, theirs])
        await session.flush()
        session.add_all(
            [
                Line(
                    customer=direct,
                    service=Service.LIVE,
                    monthly_price_cents=1000,
                    started_on=date(2025, 1, 1),
                    expires_on=TODAY,
                    anchor_day=1,
                ),
                Line(
                    customer=theirs,
                    service=Service.LIVE_VOD,
                    monthly_price_cents=700,
                    started_on=date(2025, 1, 1),
                    expires_on=TODAY,
                    anchor_day=1,
                    reseller_id=reseller.id,
                ),
            ]
        )
        await session.commit()

    payload = await build_owner_reminder(TODAY, factory=factory)

    assert payload is not None
    body, markup = payload
    assert "Scadenze dirette" in body
    assert "Diretto Uno" in body
    assert "Cliente Rivenditore" not in body
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert not any((data or "").startswith("greq:") for data in callbacks)


async def test_owner_reminder_skips_reseller_exception(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        reseller = Reseller(name="Luca", name_key="luca")
        customer = Customer(name="Cliente Rivenditore", search_key="cliente rivenditore")
        session.add_all([reseller, customer])
        await session.flush()
        line = Line(
            customer=customer,
            service=Service.LIVE,
            monthly_price_cents=700,
            started_on=date(2025, 1, 1),
            expires_on=date(2027, 5, 1),
            anchor_day=1,
            reseller_id=reseller.id,
        )
        session.add(line)
        await session.flush()
        session.add(
            ReviewException(line_id=line.id, reason="decidere col cliente", review_on=TODAY)
        )
        await session.commit()

    assert await build_owner_reminder(TODAY, factory=factory) is None
