"""Pulsantiera del gruppo: `/start` e il menu a bottoni."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import app.db as db_module
from app.bot import group_menu
from app.models import Customer, Line, Reseller, Service
from app.services.text import customer_search_key

OWNER_ID = 42
GROUP_ID = -100999
OTHER_GROUP = -100888


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


class FakeMessage:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def reply_html(self, text, **kwargs):
        if "parse_mode" in kwargs:
            raise TypeError("unexpected keyword argument 'parse_mode'")
        self.sent.append((text, kwargs.get("reply_markup")))
        return self


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.edits: list[str] = []
        self.markup = None
        self.message = SimpleNamespace(message_id=1)

    async def answer(self, **_kwargs):
        return None

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


def start_update(chat):
    return SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=7771, first_name="Mario", full_name="Mario Rossi"),
        effective_chat=chat,
        effective_message=FakeMessage(),
    )


def callback_update(data: str, chat):
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(id=7771, first_name="Mario", full_name="Mario Rossi"),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


GROUP = SimpleNamespace(id=GROUP_ID, type="supergroup", title="Gruppo Mario")


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def _seed(session, *, group_chat_id: int | None = GROUP_ID, expiring: int = 0):
    reseller = Reseller(
        name="Mario",
        name_key="mario",
        group_chat_id=group_chat_id,
        active=True,
        notify=True,
    )
    session.add(reseller)
    await session.flush()
    today = date.today()
    for index in range(max(expiring, 1)):
        customer = Customer(
            name=f"Cliente {index + 1}", search_key=customer_search_key(f"Cliente {index + 1}")
        )
        session.add(customer)
        await session.flush()
        session.add(
            Line(
                customer=customer,
                service=Service.LIVE,
                monthly_price_cents=500,
                started_on=date(2025, 1, 1),
                expires_on=today + timedelta(days=index)
                if expiring
                else today + timedelta(days=40),
                anchor_day=1,
                reseller_id=reseller.id,
            )
        )
    await session.commit()
    return reseller


async def test_start_in_group_shows_the_keyboard(session, bot_session_factory) -> None:
    await _seed(session)
    update = start_update(GROUP)

    handled = await group_menu.start(update, SimpleNamespace(user_data={}))

    assert handled is True
    body, markup = update.effective_message.sent[-1]
    assert "Scadenze" in body and "Rinnovo" in body
    assert _callbacks(markup) == ["gmenu:due", "gmenu:renew", "gmenu:help"]


async def test_start_in_unlinked_group_explains_what_is_missing(
    session, bot_session_factory
) -> None:
    await _seed(session, group_chat_id=None)
    chat = SimpleNamespace(id=OTHER_GROUP, type="supergroup", title="Altro")
    update = start_update(chat)

    handled = await group_menu.start(update, SimpleNamespace(user_data={}))

    assert handled is True
    body, markup = update.effective_message.sent[-1]
    assert "non è ancora collegato" in body
    assert markup is None


async def test_owner_sees_the_same_menu_in_the_group(session, bot_session_factory) -> None:
    """Il menu appartiene alla chat, non a chi preme: vale anche per l'owner."""
    await _seed(session)
    update = start_update(GROUP)
    update.effective_user = SimpleNamespace(id=OWNER_ID, first_name="Danilo", full_name="Danilo")

    handled = await group_menu.start(update, SimpleNamespace(user_data={}))

    assert handled is True
    body, markup = update.effective_message.sent[-1]
    assert "Mario" in body
    assert _callbacks(markup) == ["gmenu:due", "gmenu:renew", "gmenu:help"]


async def _three_sources(session):
    """Linea del gruppo A, linea del gruppo B, linea diretta: tutte in scadenza."""
    today = date.today()
    out = {}
    for key, group_id in (("a", GROUP_ID), ("b", OTHER_GROUP), ("diretto", None)):
        reseller = None
        if group_id is not None:
            reseller = Reseller(
                name=f"Reseller {key}",
                name_key=f"reseller {key}",
                group_chat_id=group_id,
                active=True,
                notify=True,
            )
            session.add(reseller)
            await session.flush()
        customer = Customer(name=f"Cliente {key}", search_key=customer_search_key(f"Cliente {key}"))
        session.add(customer)
        await session.flush()
        line = Line(
            customer=customer,
            service=Service.LIVE,
            monthly_price_cents=500,
            started_on=date(2025, 1, 1),
            expires_on=today,
            anchor_day=1,
            reseller_id=reseller.id if reseller else None,
        )
        session.add(line)
        await session.flush()
        out[key] = line.id
    await session.commit()
    return out


async def test_owner_in_group_sees_only_that_reseller(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    update = callback_update("gmenu:renew", GROUP)
    update.effective_user = SimpleNamespace(id=OWNER_ID, first_name="Danilo", full_name="Danilo")

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    callbacks = _callbacks(update.callback_query.markup)
    offered = [c for c in callbacks if c.startswith("greq:")]
    assert offered == [f"greq:{lines['a']}"]


async def test_owner_in_group_scadenze_shows_only_that_reseller(
    session, bot_session_factory
) -> None:
    await _three_sources(session)
    update = callback_update("gmenu:due", GROUP)
    update.effective_user = SimpleNamespace(id=OWNER_ID, first_name="Danilo", full_name="Danilo")

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "Cliente a" in body
    assert "Cliente b" not in body and "Cliente diretto" not in body


async def test_start_in_private_is_not_handled(session, bot_session_factory) -> None:
    await _seed(session)
    chat = SimpleNamespace(id=7771, type="private")
    handled = await group_menu.start(start_update(chat), SimpleNamespace(user_data={}))
    assert handled is False


async def test_menu_scadenze_lists_lines_with_request_buttons(session, bot_session_factory) -> None:
    await _seed(session, expiring=2)
    update = callback_update("gmenu:due", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "Cliente 1" in body and "Cliente 2" in body
    callbacks = _callbacks(update.callback_query.markup)
    assert len([c for c in callbacks if c.startswith("greq:")]) == 2
    assert callbacks[-1] == "gmenu:home"


async def test_menu_scadenze_says_when_nothing_is_due(session, bot_session_factory) -> None:
    await _seed(session, expiring=0)
    update = callback_update("gmenu:due", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    assert "Nessuna scadenza" in update.callback_query.edits[-1]
    assert _callbacks(update.callback_query.markup) == ["gmenu:home"]


async def test_menu_rinnovo_lists_line_buttons_without_prices(session, bot_session_factory) -> None:
    await _seed(session, expiring=0)
    update = callback_update("gmenu:renew", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "Quale linea vuoi rinnovare?" in body
    assert "€" not in body, "in gruppo i prezzi restano in privato"
    callbacks = _callbacks(update.callback_query.markup)
    assert len([c for c in callbacks if c.startswith("greq:")]) == 1
    assert callbacks[-1] == "gmenu:home"


async def test_menu_help_returns_to_the_menu(session, bot_session_factory) -> None:
    await _seed(session)
    update = callback_update("gmenu:help", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    assert "Come funziona" in update.callback_query.edits[-1]
    assert _callbacks(update.callback_query.markup) == ["gmenu:home"]


async def test_menu_refuses_unlinked_group(session, bot_session_factory) -> None:
    await _seed(session, group_chat_id=None, expiring=0)
    chat = SimpleNamespace(id=OTHER_GROUP, type="supergroup", title="Altro")
    update = callback_update("gmenu:due", chat)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    assert "non è collegato" in update.callback_query.edits[-1]
