"""Riepilogo giornaliero nel gruppo del rivenditore.

Nel gruppo arriva un messaggio con le scadenze dei prossimi `reminder_days`
giorni (default 3), solo per le linee del rivenditore collegato a quel gruppo.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.jobs.reminders import (
    _today as job_today,
)
from app.jobs.reminders import (
    build_group_reminder,
    reminder_job,
)
from app.models import Customer, Line, Reseller, Service
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)
OWNER_ID = 42


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", str(OWNER_ID))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def bot_session_factory(engine, monkeypatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


async def _reseller(session, name: str, *, group_id: int | None) -> Reseller:
    reseller = Reseller(
        name=name,
        name_key=name.lower(),
        group_chat_id=group_id,
        active=True,
        notify=True,
    )
    session.add(reseller)
    await session.flush()
    return reseller


async def _line(session, reseller_id: int | None, *, expires: date, name: str) -> Line:
    customer = Customer(name=name, search_key=customer_search_key(name))
    session.add(customer)
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=expires,
        anchor_day=1,
        reseller_id=reseller_id,
    )
    session.add(line)
    await session.commit()
    return line


async def test_group_message_lists_only_its_reseller_and_window(session, engine) -> None:
    luca = await _reseller(session, "Luca", group_id=-100999)
    anna = await _reseller(session, "Anna", group_id=-100888)
    await _line(session, luca.id, expires=date(2026, 11, 4), name="Mario Rossi")
    await _line(session, luca.id, expires=date(2026, 12, 20), name="Fuori Finestra")
    await _line(session, anna.id, expires=date(2026, 11, 3), name="Altro Rivenditore")
    await _line(session, None, expires=date(2026, 11, 3), name="Diretto")

    built = await build_group_reminder(
        TODAY, reseller_id=luca.id, chat_id=-100999, factory=async_sessionmaker(engine)
    )

    assert built is not None
    body, markup = built
    assert "Mario Rossi" in body
    assert "Altro Rivenditore" not in body
    assert "Fuori Finestra" not in body
    assert "Diretto" not in body  # le linee dirette non sono del rivenditore
    assert "prossimi 3 giorni" in body
    assert markup is None, "in gruppo niente pulsanti da owner"


async def test_group_message_says_when_each_line_expires(session, engine) -> None:
    luca = await _reseller(session, "Luca", group_id=-100999)
    await _line(session, luca.id, expires=TODAY, name="Oggi Cliente")
    await _line(session, luca.id, expires=date(2026, 11, 3), name="Domani Cliente")

    built = await build_group_reminder(
        TODAY, reseller_id=luca.id, chat_id=-100999, factory=async_sessionmaker(engine)
    )

    assert built is not None
    body = built[0]
    assert "scade oggi" in body
    assert "scade domani" in body


async def test_no_message_when_nothing_is_due(session, engine) -> None:
    luca = await _reseller(session, "Luca", group_id=-100999)
    await _line(session, luca.id, expires=date(2027, 6, 1), name="Lontano")

    built = await build_group_reminder(
        TODAY, reseller_id=luca.id, chat_id=-100999, factory=async_sessionmaker(engine)
    )

    assert built is None


async def test_same_day_second_run_does_not_repeat(session, engine) -> None:
    luca = await _reseller(session, "Luca", group_id=-100999)
    await _line(session, luca.id, expires=TODAY, name="Mario Rossi")
    factory = async_sessionmaker(engine)

    first = await build_group_reminder(TODAY, reseller_id=luca.id, chat_id=-100999, factory=factory)
    second = await build_group_reminder(
        TODAY, reseller_id=luca.id, chat_id=-100999, factory=factory
    )

    assert first is not None
    assert second is None


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


async def test_job_sends_to_each_linked_group_only(session, engine, bot_session_factory) -> None:
    # Il job usa la data reale: le linee si seminano rispetto a quella.
    today = job_today()
    luca = await _reseller(session, "Luca", group_id=-100999)
    anna = await _reseller(session, "Anna", group_id=None)  # nessun gruppo
    await _line(session, luca.id, expires=today, name="Mario Rossi")
    await _line(session, anna.id, expires=today, name="Cliente Di Anna")

    bot = FakeBot()
    context = SimpleNamespace(bot=bot)

    await reminder_job(context)

    chat_ids = [chat_id for chat_id, _text in bot.sent]
    assert -100999 in chat_ids
    group_messages = [text for chat_id, text in bot.sent if chat_id == -100999]
    assert group_messages
    assert "Mario Rossi" in group_messages[0]
    assert "Cliente Di Anna" not in group_messages[0]
    # Senza gruppo non si inventa una chat.
    assert all(chat_id != anna.telegram_user_id for chat_id in chat_ids)
