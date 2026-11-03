"""Spostare un cliente da «Diretto» a un rivenditore, e viceversa.

Il rivenditore vive sulla linea: i pannelli non lo conoscono, quindi il sync non
lo sovrascrive e una modifica fatta dal bot resta. Questi test coprono il
servizio e il percorso reale dei bottoni.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import customer as customer_bot
from app.bot import editline, ui
from app.models import AuditLog, Customer, Line, Reseller, Service
from app.services.anagrafica import (
    AnagraficaError,
    set_customer_reseller,
    set_line_reseller,
)
from app.services.text import customer_search_key

OWNER_ID = 42
TODAY = date(2026, 11, 2)


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


async def _seed(session):
    luca = Reseller(name="Luca", name_key="luca", active=True)
    chiuso = Reseller(name="Chiuso", name_key="chiuso", active=False)
    customer = Customer(name="Mario Caterino", search_key=customer_search_key("Mario Caterino"))
    session.add_all([luca, chiuso, customer])
    await session.flush()

    lines = []
    for label, price in (("Linea 1", 500), ("Linea 2", 600)):
        line = Line(
            customer=customer,
            service=Service.LIVE,
            monthly_price_cents=price,
            started_on=date(2025, 1, 1),
            expires_on=date(2027, 1, 1),
            anchor_day=1,
            label=label,
        )
        session.add(line)
        lines.append(line)
    old = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=300,
        started_on=date(2024, 1, 1),
        expires_on=date(2025, 1, 1),
        anchor_day=1,
        label="Vecchia",
        deleted_at=__import__("datetime").datetime(2026, 1, 1),
    )
    session.add(old)
    await session.flush()
    return customer, luca, chiuso, lines, old


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered: list[str] = []
        self.edits: list[str] = []
        self.markup = None

    async def answer(self, text=None, **kwargs):
        self.answered.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")


def callback_update(data: str) -> SimpleNamespace:
    chat = SimpleNamespace(id=OWNER_ID, type="private")
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


def button_datas(markup) -> list[str]:
    if markup is None:
        return []
    return [button.callback_data for row in markup.inline_keyboard for button in row]


# --- servizio --------------------------------------------------------------


async def test_line_moves_to_reseller(session) -> None:
    _customer, luca, _chiuso, lines, _old = await _seed(session)
    line = lines[0]
    assert line.reseller_id is None

    changed_line, changed = await set_line_reseller(session, line.id, luca.id)
    await session.flush()

    assert changed is True
    assert changed_line.reseller_id == luca.id
    audit = await session.scalar(
        select(AuditLog).where(
            AuditLog.action == "line_reseller_set", AuditLog.entity_id == line.id
        )
    )
    assert audit is not None
    assert audit.payload == {"old": None, "new": luca.id, "reseller": "Luca"}


async def test_line_back_to_direct(session) -> None:
    _customer, luca, _chiuso, lines, _old = await _seed(session)
    line = lines[0]
    await set_line_reseller(session, line.id, luca.id)
    await session.flush()

    changed_line, changed = await set_line_reseller(session, line.id, None)
    await session.flush()

    assert changed is True
    assert changed_line.reseller_id is None


async def test_same_reseller_is_not_a_change(session) -> None:
    _customer, luca, _chiuso, lines, _old = await _seed(session)
    line = lines[0]
    await set_line_reseller(session, line.id, luca.id)
    await session.flush()

    _line, changed = await set_line_reseller(session, line.id, luca.id)

    assert changed is False


async def test_inactive_or_unknown_reseller_is_refused(session) -> None:
    _customer, _luca, chiuso, lines, _old = await _seed(session)
    line = lines[0]

    with pytest.raises(AnagraficaError):
        await set_line_reseller(session, line.id, chiuso.id)
    with pytest.raises(AnagraficaError):
        await set_line_reseller(session, line.id, 9999)


async def test_customer_move_covers_all_active_lines(session) -> None:
    customer, luca, _chiuso, lines, old = await _seed(session)

    changed, total = await set_customer_reseller(session, customer.id, luca.id)
    await session.flush()

    # Le due linee attive, non quella cancellata.
    assert (changed, total) == (2, 2)
    assert old.reseller_id is None
    for line in lines:
        assert line.reseller_id == luca.id

    entries = list(
        (
            await session.scalars(select(AuditLog).where(AuditLog.action == "line_reseller_set"))
        ).all()
    )
    assert len(entries) == 2
    assert all(entry.payload["via"] == "customer" for entry in entries)
    summary = await session.scalar(
        select(AuditLog).where(AuditLog.action == "customer_reseller_set")
    )
    assert summary is not None
    assert summary.payload["lines"] == 2


async def test_customer_move_is_idempotent(session) -> None:
    customer, luca, _chiuso, _lines, _old = await _seed(session)
    await set_customer_reseller(session, customer.id, luca.id)
    await session.flush()

    changed, total = await set_customer_reseller(session, customer.id, luca.id)

    assert (changed, total) == (0, 2)


# --- bot ------------------------------------------------------------------


async def test_line_picker_shows_current_reseller(session, bot_session_factory) -> None:
    _customer, luca, _chiuso, lines, _old = await _seed(session)
    line = lines[0]
    await set_line_reseller(session, line.id, luca.id)
    await session.commit()

    update = callback_update(f"edit:{line.id}")
    await editline.on_callback(update, SimpleNamespace(user_data={}))

    markup = update.callback_query.markup
    datas = button_datas(markup)
    assert f"edit:{line.id}:reseller" in datas
    # Il valore attuale sta sull'etichetta del pulsante.
    labels = [button.text for row in markup.inline_keyboard for button in row]
    assert any("Rivenditore" in label and "Luca" in label for label in labels)


async def test_line_reseller_callback_applies(session, bot_session_factory) -> None:
    _customer, luca, _chiuso, lines, _old = await _seed(session)
    line = lines[0]
    await session.commit()

    context = SimpleNamespace(user_data={})
    update = callback_update(f"edit:{line.id}:rset:{luca.id}")
    await editline.on_callback(update, context)

    assert update.callback_query.edits
    assert "Passata a" in update.callback_query.edits[-1]
    async with bot_session_factory() as check:
        fresh = await check.get(Line, line.id)
        assert fresh is not None
        assert fresh.reseller_id == luca.id


async def test_direct_callback_removes_assignment(session, bot_session_factory) -> None:
    _customer, luca, _chiuso, lines, _old = await _seed(session)
    line = lines[0]
    await set_line_reseller(session, line.id, luca.id)
    await session.commit()

    update = callback_update(f"edit:{line.id}:rset:0")
    await editline.on_callback(update, SimpleNamespace(user_data={}))

    async with bot_session_factory() as check:
        fresh = await check.get(Line, line.id)
        assert fresh is not None
        assert fresh.reseller_id is None


async def test_customer_card_has_reseller_button_for_owner(session) -> None:
    customer, _luca, _chiuso, _lines, _old = await _seed(session)
    await session.commit()

    from app.services.queries import customer_overview

    overview = await customer_overview(session, customer.id)
    assert overview is not None

    owner = button_datas(ui.customer_keyboard(overview, is_owner=True))
    reseller = button_datas(ui.customer_keyboard(overview, is_owner=False))
    assert f"custres:{customer.id}" in owner
    assert f"custres:{customer.id}" not in reseller


async def test_customer_reseller_callback_applies_to_all(session, bot_session_factory) -> None:
    customer, luca, _chiuso, _lines, _old = await _seed(session)
    await session.commit()

    update = callback_update(f"custres:{customer.id}:{luca.id}")
    await customer_bot.on_reseller_callback(update, SimpleNamespace(user_data={}))

    assert "2 linee aggiornate" in update.callback_query.edits[-1]
    async with bot_session_factory() as check:
        fresh = list(
            (
                await check.scalars(
                    select(Line).where(Line.customer_id == customer.id, Line.deleted_at.is_(None))
                )
            ).all()
        )
    assert all(line.reseller_id == luca.id for line in fresh)


async def test_customer_picker_lists_resellers_and_direct(session, bot_session_factory) -> None:
    customer, luca, _chiuso, _lines, _old = await _seed(session)
    await session.commit()

    update = callback_update(f"custres:{customer.id}")
    await customer_bot.on_reseller_callback(update, SimpleNamespace(user_data={}))

    datas = button_datas(update.callback_query.markup)
    assert f"custres:{customer.id}:0" in datas
    assert f"custres:{customer.id}:{luca.id}" in datas
    # Il rivenditore disattivato non si propone.
    assert not any(data.endswith(":2") for data in datas)
