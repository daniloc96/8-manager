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
    def __init__(self, text: str | None = None) -> None:
        self.sent: list[tuple[str, object]] = []
        self.text = text

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
    assert _callbacks(markup) == [
        "gmenu:due",
        "gmenu:renew",
        "gmenu:search",
        "gmenu:help",
    ]


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
    assert _callbacks(markup) == [
        "gmenu:due",
        "gmenu:renew",
        "gmenu:search",
        "gmenu:help",
    ]


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


def text_update(text: str, chat):
    return SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=7771, first_name="Mario", full_name="Mario Rossi"),
        effective_chat=chat,
        effective_message=FakeMessage(text),
    )


async def test_search_button_asks_for_a_name(session, bot_session_factory) -> None:
    await _seed(session)
    update = callback_update("gmenu:search", GROUP)
    context = SimpleNamespace(user_data={})

    await group_menu.on_callback(update, context)

    assert "Chi cerchi?" in update.callback_query.edits[-1]
    assert context.user_data["awaiting"] == group_menu.SEARCH_HINT


async def test_search_answers_with_one_button_per_customer(session, bot_session_factory) -> None:
    await _three_sources(session)
    update = callback_update("gmenu:search", GROUP)
    context = SimpleNamespace(user_data={})
    await group_menu.on_callback(update, context)

    typed = text_update("cliente", GROUP)
    handled = await group_menu.handle_text(typed, context)

    assert handled is True
    assert "awaiting" not in context.user_data
    body, markup = typed.effective_message.sent[-1]
    assert "Chi è cliente?" in body
    assert "1 cliente trovato" in body
    callbacks = _callbacks(markup)
    # un solo pulsante-cliente, e nessun pulsante d'azione nella ricerca
    assert len([c for c in callbacks if c.startswith("gcard:")]) == 1
    assert not [c for c in callbacks if c.startswith(("grenew:", "gdue:", "gcod:"))]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "Cliente a" in labels, "il pulsante è il nome del cliente"
    assert "Cliente b" not in labels and "Cliente diretto" not in labels
    assert callbacks[-2:] == ["gmenu:search", "gmenu:home"]


async def test_search_with_no_hits_says_so(session, bot_session_factory) -> None:
    await _seed(session)
    context = SimpleNamespace(user_data={"awaiting": group_menu.SEARCH_HINT})
    typed = text_update("nessuno", GROUP)

    handled = await group_menu.handle_text(typed, context)

    assert handled is True
    body, _markup = typed.effective_message.sent[-1]
    assert "Nessun cliente trovato" in body


async def test_text_without_search_pending_is_ignored(session, bot_session_factory) -> None:
    await _seed(session)
    typed = text_update("ciao", GROUP)

    handled = await group_menu.handle_text(typed, SimpleNamespace(user_data={}))

    assert handled is False
    assert typed.effective_message.sent == []


async def test_cerca_command_uses_group_scoped_search(session, bot_session_factory) -> None:
    """`/cerca nome` in gruppo: stesso risultato del menu, senza privacy off."""
    from app.bot import search

    await _three_sources(session)
    update = text_update("/cerca cliente", GROUP)
    update.effective_message = FakeMessage("/cerca cliente")
    context = SimpleNamespace(user_data={}, args=["cliente"])

    await search.cerca(update, context)

    markup = update.effective_message.sent[-1][1]
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert "Cliente a" in labels
    assert "Cliente b" not in labels and "Cliente diretto" not in labels
    assert len([c for c in _callbacks(markup) if c.startswith("gcard:")]) == 1


async def test_cerca_command_without_name_says_how_to_use_it(session, bot_session_factory) -> None:
    from app.bot import search

    update = text_update("/cerca", GROUP)
    update.effective_message = FakeMessage("/cerca")

    await search.cerca(update, SimpleNamespace(user_data={}, args=[]))

    body, _markup = update.effective_message.sent[-1]
    assert "/cerca nome del cliente" in body


async def test_card_shows_actions_for_the_customer(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    customer_id = await _customer_of(session, lines["a"])
    update = callback_update(f"gcard:{customer_id}", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "Cliente a" in body
    assert "€" not in body, "in gruppo i prezzi restano in privato"
    assert _callbacks(update.callback_query.markup) == [
        f"gdue:{customer_id}",
        f"gcod:{customer_id}",
        f"grenew:{customer_id}",
        "gmenu:home",
    ]


async def test_card_codes_button_shows_credentials(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    customer_id = await _customer_of(session, lines["a"])
    update = callback_update(f"gcod:{customer_id}", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "Codici · Cliente a" in body
    assert "€" not in body
    assert _callbacks(update.callback_query.markup)[0] == f"gcard:{customer_id}"


async def test_card_due_button_shows_dates(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    customer_id = await _customer_of(session, lines["a"])
    update = callback_update(f"gdue:{customer_id}", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "Scadenze · Cliente a" in body
    assert "oggi" in body
    assert "scade il" not in body.split("\n")[-1], "la data non si ripete due volte"
    assert _callbacks(update.callback_query.markup)[0] == f"gcard:{customer_id}"


async def test_codes_of_another_reseller_is_refused(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    customer_id = await _customer_of(session, lines["b"])
    update = callback_update(f"gcod:{customer_id}", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    assert "non disponibile in questo gruppo" in update.callback_query.edits[-1]


async def test_card_refuses_a_customer_of_another_reseller(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    customer_id = await _customer_of(session, lines["b"])
    update = callback_update(f"gcard:{customer_id}", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    assert "non disponibile in questo gruppo" in update.callback_query.edits[-1]


async def test_renew_for_customer_lists_only_their_lines(session, bot_session_factory) -> None:
    lines = await _three_sources(session)
    customer_id = await _customer_of(session, lines["a"])
    update = callback_update(f"grenew:{customer_id}", GROUP)

    await group_menu.on_callback(update, SimpleNamespace(user_data={}))

    assert "Quale linea di Cliente a" in update.callback_query.edits[-1]
    callbacks = _callbacks(update.callback_query.markup)
    assert [c for c in callbacks if c.startswith("greq:")] == [f"greq:{lines['a']}"]


async def _customer_of(session, line_id: int) -> int:
    from app.models import Line

    line = await session.get(Line, line_id)
    return line.customer_id


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
