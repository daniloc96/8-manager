from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import editline
from app.models import Customer, Line, PanelLine, Platform, Service
from app.services.text import customer_search_key

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


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.text: str | None = None
        self.markup = None
        self.alerts: list[str] = []

    async def answer(self, text=None, **kwargs) -> None:
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, **kwargs) -> None:
        self.text = text
        self.markup = kwargs.get("reply_markup")


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.sent: list[str] = []
        self.markup = None

    async def reply_html(self, text, **kwargs):
        self.sent.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


def _callback(data: str, user_id: int = OWNER_ID):
    query = FakeQuery(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=None,
    )
    return update, query


def _text_update(text: str, user_id: int = OWNER_ID):
    message = FakeMessage(text)
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
    )
    return update, message


async def _seed(session):
    customer = Customer(name="Mario Caterino", search_key=customer_search_key("Mario Caterino"))
    session.add(customer)
    await session.flush()
    first = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        label="Linea 1",
    )
    second = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=600,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        label="Linea 2",
    )
    session.add_all([first, second])
    await session.flush()
    session.add(
        PanelLine(
            platform=Platform.LIVE_HALLO,
            username="mario2026",
            username_key="mario2026",
            line_id=first.id,
        )
    )
    await session.commit()
    return customer, first, second


async def test_picker_lists_editable_fields(session, bot_session_factory) -> None:
    _customer, first, _second = await _seed(session)
    update, query = _callback(f"edit:{first.id}")

    await editline.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    data = [b.callback_data for row in query.markup.inline_keyboard for b in row]
    assert f"edit:{first.id}:alias" in data
    assert f"edit:{first.id}:label" in data
    assert f"edit:{first.id}:notes" in data
    assert f"edit:{first.id}:tech" in data
    assert f"card:{first.id}" in data


async def test_choose_alias_sets_awaiting_state(session, bot_session_factory) -> None:
    _customer, first, _second = await _seed(session)
    update, query = _callback(f"edit:{first.id}:alias")
    context = SimpleNamespace(user_data={}, bot=None)

    await editline.on_callback(update, context)

    assert context.user_data["awaiting"] == f"lineedit:{first.id}:alias"
    assert "alias" in query.text.lower()


async def test_alias_text_updates_customer(session, bot_session_factory) -> None:
    customer, first, _second = await _seed(session)
    update, message = _text_update("Mario il rosso")
    context = SimpleNamespace(user_data={"awaiting": f"lineedit:{first.id}:alias"}, bot=None)

    consumed = await editline.handle_text(update, context)

    assert consumed is True
    assert "awaiting" not in context.user_data
    assert message.sent and "Alias" in message.sent[0]

    async with bot_session_factory() as check:
        refreshed = await check.get(Customer, customer.id)
        assert refreshed.alias == "Mario il rosso"
        assert refreshed.search_key == customer_search_key("Mario Caterino", "Mario il rosso")


async def test_dash_clears_value(session, bot_session_factory) -> None:
    customer, first, _second = await _seed(session)
    first_update, _first_message = _text_update("Pinco")
    await editline.handle_text(
        first_update,
        SimpleNamespace(user_data={"awaiting": f"lineedit:{first.id}:alias"}, bot=None),
    )
    update, _message = _text_update("-")
    context = SimpleNamespace(user_data={"awaiting": f"lineedit:{first.id}:alias"}, bot=None)

    await editline.handle_text(update, context)

    async with bot_session_factory() as check:
        refreshed = await check.get(Customer, customer.id)
        assert refreshed.alias is None


async def test_duplicate_label_keeps_state_for_retry(session, bot_session_factory) -> None:
    _customer, first, second = await _seed(session)
    update, message = _text_update("Linea 2")
    context = SimpleNamespace(user_data={"awaiting": f"lineedit:{first.id}:label"}, bot=None)

    consumed = await editline.handle_text(update, context)

    assert consumed is True
    assert context.user_data["awaiting"] == f"lineedit:{first.id}:label"
    assert message.sent and "Linea 2" in message.sent[0]

    async with bot_session_factory() as check:
        refreshed = await check.get(Line, second.id)
        assert refreshed.label == "Linea 2"


async def test_panel_code_from_callback_and_text(session, bot_session_factory) -> None:
    _customer, first, _second = await _seed(session)
    update, query = _callback(f"edit:{first.id}:code:live_hallo")
    context = SimpleNamespace(user_data={}, bot=None)

    await editline.on_callback(update, context)

    assert context.user_data["awaiting"] == f"lineedit:{first.id}:access_code:live_hallo"
    assert "8Plus" in query.text or "Live" in query.text

    text_update, _message = _text_update("ZZ99")
    await editline.handle_text(text_update, context)

    assert "awaiting" not in context.user_data
    async with bot_session_factory() as check:
        from sqlalchemy import select

        panel = await check.scalar(select(PanelLine).where(PanelLine.line_id == first.id))
        assert panel.access_code == "ZZ99"


async def test_free_text_is_routed_by_search_handler(session, bot_session_factory) -> None:
    """Il testo libero passa da search.on_text: l'edit deve essere consumato lì."""
    customer, first, _second = await _seed(session)
    update, message = _text_update("Pinco")
    context = SimpleNamespace(user_data={"awaiting": f"lineedit:{first.id}:alias"}, bot=None)

    from app.bot import search

    await search.on_text(update, context)

    assert "awaiting" not in context.user_data
    # Se fosse finita nella ricerca libera, non ci sarebbe la conferma dell'alias.
    assert message.sent and "Alias" in message.sent[0]

    async with bot_session_factory() as check:
        refreshed = await check.get(Customer, customer.id)
        assert refreshed.alias == "Pinco"


async def test_non_owner_cannot_edit(session, bot_session_factory) -> None:
    _customer, first, _second = await _seed(session)
    update, query = _callback(f"edit:{first.id}", user_id=999)

    await editline.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.alerts and "owner" in query.alerts[0]
    assert query.text is None


async def test_cancel_returns_to_card(session, bot_session_factory) -> None:
    _customer, first, _second = await _seed(session)
    update, query = _callback(f"edit:{first.id}:cancel")
    context = SimpleNamespace(user_data={"awaiting": f"lineedit:{first.id}:alias"}, bot=None)

    await editline.on_callback(update, context)

    assert "awaiting" not in context.user_data
    assert query.text is not None
    assert "Mario Caterino" in query.text
