from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import menu
from app.models import Customer, Line, Reseller, Service, Setting
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
    """Sta al posto di telegram.CallbackQuery: stesso metodo async."""

    def __init__(self, data: str) -> None:
        self.data = data
        self.text: str | None = None
        self.markup = None

    async def answer(self, *args, **kwargs) -> None:
        return None

    async def edit_message_text(self, text, **kwargs) -> None:
        self.text = text
        self.markup = kwargs.get("reply_markup")


class FakeMessage:
    """Sta al posto di telegram.Message: reply_html è una coroutine."""

    def __init__(self) -> None:
        self.text: str | None = None
        self.markup = None

    async def reply_html(self, text, **kwargs):
        self.text = text
        self.markup = kwargs.get("reply_markup")
        return self


def _callback_update(data: str, user_id: int = OWNER_ID):
    query = FakeQuery(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=None,
    )
    return update, query


def _command_update(user_id: int = OWNER_ID):
    message = FakeMessage()
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
    )
    return update, message


async def _seed(session) -> None:
    reseller = Reseller(name="Luca", name_key="luca")
    mario = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    anna = Customer(name="Anna Bianchi", search_key=customer_search_key("Anna Bianchi"))
    session.add_all([reseller, mario, anna])
    await session.flush()
    session.add_all(
        [
            Line(
                customer=mario,
                service=Service.LIVE_VOD,
                monthly_price_cents=500,
                started_on=date(2025, 1, 1),
                expires_on=date(2026, 11, 2),
                anchor_day=1,
                reseller_id=reseller.id,
            ),
            Line(
                customer=anna,
                service=Service.LIVE,
                monthly_price_cents=700,
                started_on=date(2025, 1, 1),
                expires_on=date(2026, 11, 30),
                anchor_day=1,
            ),
        ]
    )
    session.add(Setting(key="vod_domain", value="http://vod.example.org"))
    await session.commit()


async def test_menu_home_from_button(session, bot_session_factory) -> None:
    await _seed(session)
    update, query = _callback_update("menu:home")

    await menu.menu_home(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Menu" in query.text


async def test_show_due_from_button(session, bot_session_factory) -> None:
    await _seed(session)
    update, query = _callback_update("menu:due")

    await menu.show_due(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Scadenze" in query.text


async def test_show_due_from_command(session, bot_session_factory) -> None:
    """Il comando /scadenze non ha callback_query: risponde al messaggio."""
    await _seed(session)
    update, message = _command_update()

    await menu.show_due(update, SimpleNamespace(user_data={}, bot=None))

    assert message.text is not None
    assert "Scadenze" in message.text


async def test_show_resellers_from_button(session, bot_session_factory) -> None:
    await _seed(session)
    update, query = _callback_update("menu:resellers")

    await menu.show_resellers(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Luca" in query.text


async def test_show_settings_from_button(session, bot_session_factory) -> None:
    await _seed(session)
    update, query = _callback_update("menu:settings")

    await menu.show_settings(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Dominio VOD" in query.text
    assert "http://vod.example.org" in query.text
    data = [b.callback_data for row in query.markup.inline_keyboard for b in row]
    assert "set:vod_domain" in data


async def test_income_from_command(session, bot_session_factory) -> None:
    await _seed(session)
    update, message = _command_update()

    await menu.show_income(update, SimpleNamespace(user_data={}, bot=None))

    assert message.text is not None
    assert "Parco" in message.text
