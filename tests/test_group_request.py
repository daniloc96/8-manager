"""Richiesta di rinnovo dal gruppo: due tap, niente invii per sbaglio."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db as db_module
from app.bot import group_request
from app.models import Customer, Line, RenewalRequest, Reseller, Service
from app.services.text import customer_search_key

OWNER_ID = 42
PRESSER = 5551
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


class FakeQuery:
    """Come PTB: `edit_message_text`, e `answer` per togliere l'orologio."""

    def __init__(self, data: str, message_id: int = 555) -> None:
        self.data = data
        self.message = SimpleNamespace(message_id=message_id)
        self.edits: list[str] = []
        self.markup = None
        self.answered = 0

    async def answer(self, **_kwargs):
        self.answered += 1

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


def callback_update(data: str, *, chat, user_id: int = PRESSER):
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(
            id=user_id, first_name="Anna", username="anna", full_name="Anna Bianchi"
        ),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


GROUP = SimpleNamespace(id=GROUP_ID, type="supergroup", title="Gruppo Mario")


async def _seed(session, *, with_group: bool = True):
    reseller = Reseller(
        name="Mario",
        name_key="mario",
        group_chat_id=GROUP_ID if with_group else None,
        active=True,
        notify=True,
    )
    session.add(reseller)
    await session.flush()
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    session.add(customer)
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2026, 11, 4),
        anchor_day=1,
        reseller_id=reseller.id,
    )
    session.add(line)
    await session.commit()
    return reseller, line


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def _requests(factory) -> list[RenewalRequest]:
    async with factory() as check:
        return list((await check.scalars(select(RenewalRequest))).all())


async def test_first_tap_asks_without_creating_anything(session, bot_session_factory) -> None:
    _reseller, line = await _seed(session)
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    update = callback_update(f"greq:{line.id}", chat=GROUP)

    await group_request.ask(update, context)

    text = update.callback_query.edits[-1]
    assert "Sei sicuro di voler richiedere il rinnovo?" in text
    assert "Mario Rossi" in text
    assert "scade 04/11/2026" in text
    assert _callbacks(update.callback_query.markup) == [
        f"greqy:{line.id}",
        f"greqn:{line.id}",
    ]
    assert await _requests(bot_session_factory) == []
    assert context.bot.sent == []


async def test_second_tap_creates_the_request_and_tells_the_owner(
    session, bot_session_factory
) -> None:
    _reseller, line = await _seed(session)
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    update = callback_update(f"greqy:{line.id}", chat=GROUP)

    await group_request.confirm(update, context)

    assert "Richiesta inviata" in update.callback_query.edits[-1]
    requests = await _requests(bot_session_factory)
    assert len(requests) == 1
    request = requests[0]
    assert request.line_id == line.id
    assert request.months == group_request.default_months()
    assert request.requested_by_telegram_id == PRESSER
    assert request.origin_chat_id == GROUP_ID
    assert request.origin_message_id == 555
    assert "Gruppo Mario" in (request.note or "")

    owner_messages = [text for chat_id, text in context.bot.sent if chat_id == OWNER_ID]
    assert owner_messages, "l'owner deve saperlo subito"
    assert "Nuova richiesta dal gruppo" in owner_messages[0]
    assert "Anna Bianchi" in owner_messages[0]


async def test_cancel_does_not_create_and_says_so(session, bot_session_factory) -> None:
    _reseller, line = await _seed(session)
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    update = callback_update(f"greqn:{line.id}", chat=GROUP)

    await group_request.cancel(update, context)

    assert "Richiesta annullata" in update.callback_query.edits[-1]
    assert await _requests(bot_session_factory) == []
    assert context.bot.sent == []


async def test_group_of_another_reseller_cannot_request(session, bot_session_factory) -> None:
    """Il gruppo è l'unica credenziale: se non è quello della linea, niente."""
    _reseller, line = await _seed(session)
    other = SimpleNamespace(id=OTHER_GROUP, type="supergroup", title="Altro gruppo")
    context = SimpleNamespace(bot=FakeBot(), user_data={})

    ask_update = callback_update(f"greq:{line.id}", chat=other)
    await group_request.ask(ask_update, context)
    confirm_update = callback_update(f"greqy:{line.id}", chat=other)
    await group_request.confirm(confirm_update, context)

    assert "non è più disponibile" in ask_update.callback_query.edits[-1]
    assert "non è più disponibile" in confirm_update.callback_query.edits[-1]
    assert await _requests(bot_session_factory) == []
    assert context.bot.sent == []


async def test_line_of_another_reseller_inside_a_group_is_refused(
    session, bot_session_factory
) -> None:
    await _seed(session)
    customer = Customer(name="Altro Cliente", search_key=customer_search_key("Altro Cliente"))
    session.add(customer)
    await session.flush()
    foreign = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2026, 11, 4),
        anchor_day=1,
        reseller_id=None,  # linea diretta, non del rivenditore del gruppo
    )
    session.add(foreign)
    await session.commit()

    context = SimpleNamespace(bot=FakeBot(), user_data={})
    update = callback_update(f"greqy:{foreign.id}", chat=GROUP)
    await group_request.confirm(update, context)

    assert "non è più disponibile" in update.callback_query.edits[-1]
    assert await _requests(bot_session_factory) == []


async def test_double_confirm_does_not_duplicate(session, bot_session_factory) -> None:
    _reseller, line = await _seed(session)
    context = SimpleNamespace(bot=FakeBot(), user_data={})

    await group_request.confirm(callback_update(f"greqy:{line.id}", chat=GROUP), context)
    await group_request.confirm(callback_update(f"greqy:{line.id}", chat=GROUP), context)

    assert len(await _requests(bot_session_factory)) == 1


async def test_pending_request_is_announced_instead_of_asking_again(
    session, bot_session_factory
) -> None:
    _reseller, line = await _seed(session)
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    await group_request.confirm(callback_update(f"greqy:{line.id}", chat=GROUP), context)

    ask_update = callback_update(f"greq:{line.id}", chat=GROUP)
    await group_request.ask(ask_update, context)

    text = ask_update.callback_query.edits[-1]
    assert "già una richiesta in attesa" in text
    assert ask_update.callback_query.markup is None
