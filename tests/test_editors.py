"""Rinnovo e richiesta devono funzionare sia da bottone sia da testo.

`CallbackQuery` ha `edit_message_text`, `Message` ha `reply_html`: i flussi che
partono da entrambi usano `ui.Editor`. Questi test usano finti oggetti con la
firma vera di PTB, così un metodo sbagliato solleva come in produzione.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db as db_module
from app.bot import lines, reseller
from app.models import Customer, Line, Renewal, RenewalRequest, Reseller, Service
from app.services.text import customer_search_key

OWNER_ID = 42
RESELLER_TG = 777
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
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


class StrictCallbackQuery:
    """Come PTB: `edit_message_text` sì, `edit_text` no."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.answers: list[str] = []
        self.edits: list[str] = []
        self.markup = None

    async def answer(self, text=None, **kwargs):
        if text:
            self.answers.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


class StrictMessage:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def reply_html(self, text, **kwargs):
        if "parse_mode" in kwargs:
            raise TypeError("unexpected keyword argument 'parse_mode'")
        self.sent.append(text)
        return self


def callback_update(data: str, *, user_id: int = OWNER_ID, chat=None) -> SimpleNamespace:
    chat = chat or SimpleNamespace(id=user_id, type="private")
    return SimpleNamespace(
        callback_query=StrictCallbackQuery(data),
        effective_user=SimpleNamespace(id=user_id, first_name="X"),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


async def _seed(session, *, reseller_id: int | None = None) -> Line:
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    session.add(customer)
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        reseller_id=reseller_id,
    )
    session.add(line)
    await session.commit()
    return line


async def test_renew_from_card_button(session, bot_session_factory) -> None:
    """Il rinnovo da bottone non deve cercare `edit_text` sul CallbackQuery."""
    line = await _seed(session)

    update = callback_update(f"renew:{line.id}:3")
    await lines.renew(update, SimpleNamespace(user_data={}))

    assert update.callback_query.edits
    assert "Rinnovo registrato" in update.callback_query.edits[-1]
    async with bot_session_factory() as check:
        renewals = list((await check.scalars(select(Renewal))).all())
        assert len(renewals) == 1


async def test_reseller_request_from_group_button(session, bot_session_factory) -> None:
    reseller_row = Reseller(name="Luca", name_key="luca", telegram_user_id=RESELLER_TG, active=True)
    session.add(reseller_row)
    await session.flush()
    line = await _seed(session, reseller_id=reseller_row.id)
    await session.commit()

    group = SimpleNamespace(id=-100999, type="supergroup", title="Gruppo Luca")
    update = callback_update(f"rqm:{line.id}:3", user_id=RESELLER_TG, chat=group)
    context = SimpleNamespace(user_data={}, bot=None)

    await reseller.confirm(update, context)

    assert update.callback_query.edits
    assert "Richiesta inviata" in update.callback_query.edits[-1]
    async with bot_session_factory() as check:
        requests = list((await check.scalars(select(RenewalRequest))).all())
        assert len(requests) == 1


async def test_reseller_request_from_text_still_works(session, bot_session_factory) -> None:
    reseller_row = Reseller(name="Luca", name_key="luca", telegram_user_id=RESELLER_TG, active=True)
    session.add(reseller_row)
    await session.flush()
    line = await _seed(session, reseller_id=reseller_row.id)
    await session.commit()

    chat = SimpleNamespace(id=RESELLER_TG, type="private")
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=RESELLER_TG, first_name="Luca"),
        effective_chat=chat,
        effective_message=StrictMessage(),
    )
    context = SimpleNamespace(user_data={"awaiting": f"reqmonths:{line.id}"}, bot=None)

    handled = await reseller.submit_from_text(update, context, line.id, 2)

    assert handled is True
    assert update.effective_message.sent
    assert "Richiesta inviata" in update.effective_message.sent[-1]


def test_editor_handles_both_targets() -> None:
    """Il contratto dell'adattatore, senza toccare Telegram."""
    import asyncio

    from app.bot.ui import Editor

    class FakeMessage:
        def __init__(self) -> None:
            self.calls = []

        async def reply_html(self, text, **kwargs):
            self.calls.append(("reply_html", text, kwargs))

    class FakeQuery:
        def __init__(self) -> None:
            self.calls = []

        async def edit_message_text(self, text, **kwargs):
            self.calls.append(("edit_message_text", text, kwargs))

    message, query = FakeMessage(), FakeQuery()
    asyncio.run(Editor(message).edit_text("<b>x</b>", parse_mode="HTML", reply_markup="m"))
    asyncio.run(Editor(query).edit_text("<b>x</b>", parse_mode="HTML", reply_markup="m"))

    assert message.calls[0][0] == "reply_html"
    assert "parse_mode" not in message.calls[0][2]
    assert message.calls[0][2]["reply_markup"] == "m"
    assert query.calls[0][0] == "edit_message_text"
    assert query.calls[0][2]["parse_mode"] == "HTML"
