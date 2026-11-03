"""/richieste e i pulsanti di approvazione nell'avviso all'owner.

Il comando esisteva nel modulo ma non era registrato: l'owner riceveva l'avviso
e poi nessuna risposta. Qui si copre il giro completo, dal messaggio al rinnovo.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.db as db_module
from app.bot import group_request, reseller
from app.models import (
    Customer,
    Line,
    Renewal,
    RenewalRequest,
    RequestStatus,
    Reseller,
    Service,
)
from app.services.queries import get_line
from app.services.text import customer_search_key

OWNER_ID = 42
GROUP_ID = -100999


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
    def __init__(self, data: str) -> None:
        self.data = data
        self.edits: list[str] = []
        self.markup = None
        self.message = SimpleNamespace(message_id=1)
        self.answered: list[str] = []

    async def answer(self, text=None, **kwargs):
        if text:
            self.answered.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


class FakeMessage:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def reply_html(self, text, **kwargs):
        if "parse_mode" in kwargs:
            raise TypeError("unexpected keyword argument 'parse_mode'")
        self.sent.append((text, kwargs.get("reply_markup")))
        return self


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, object]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs.get("reply_markup")))
        return SimpleNamespace(message_id=1)


GROUP = SimpleNamespace(id=GROUP_ID, type="supergroup", title="OTTO")


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def _seed(session):
    reseller_row = Reseller(
        name="Mario",
        name_key="mario",
        group_chat_id=GROUP_ID,
        active=True,
        notify=True,
    )
    session.add(reseller_row)
    await session.flush()
    customer = Customer(name="Angela Mario", search_key=customer_search_key("Angela Mario"))
    session.add(customer)
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2026, 11, 3),
        anchor_day=1,
        reseller_id=reseller_row.id,
    )
    session.add(line)
    await session.commit()
    return reseller_row, line


async def _make_request(session, line) -> RenewalRequest:
    request = RenewalRequest(
        line_id=line.id,
        requested_by_telegram_id=999,
        months=2,
        status=RequestStatus.PENDING,
        origin_chat_id=GROUP_ID,
    )
    session.add(request)
    await session.commit()
    return request


def test_richieste_command_is_registered(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", str(OWNER_ID))
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import COMMANDS, build_application

        application = build_application()
        commands = {
            command
            for handler in application.handlers[0]
            for command in getattr(handler, "commands", set())
        }
        assert "richieste" in commands
        assert "richieste" in {entry.command for entry in COMMANDS}
    finally:
        get_settings.cache_clear()


async def test_owner_notification_has_approve_and_reject(session, bot_session_factory) -> None:
    _reseller_row, line = await _seed(session)
    context = SimpleNamespace(bot=FakeBot(), user_data={})
    update = SimpleNamespace(
        callback_query=FakeQuery(f"greqy:{line.id}:2"),
        effective_user=SimpleNamespace(
            id=999, first_name="Danilo", username="Dani966", full_name="Danilo"
        ),
        effective_chat=GROUP,
        effective_message=SimpleNamespace(chat=GROUP),
    )

    await group_request.confirm(update, context)

    chat_id, text, markup = context.bot.sent[-1]
    assert chat_id == OWNER_ID
    assert "Nuova richiesta dal gruppo" in text
    callbacks = _callbacks(markup)
    assert callbacks[0].startswith("rqa:")
    assert callbacks[1].startswith("rqr:")


async def test_pending_list_shows_each_request_with_buttons(session, bot_session_factory) -> None:
    _reseller_row, line = await _seed(session)
    request = await _make_request(session, line)
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=FakeMessage(),
    )

    await reseller.pending_for_owner(update, SimpleNamespace(user_data={}))

    body, markup = update.effective_message.sent[-1]
    assert f"#{request.id}" in body
    assert "Angela Mario" in body
    callbacks = _callbacks(markup)
    assert callbacks == [f"rqa:{request.id}:2", f"rqr:{request.id}"]


async def test_pending_list_without_requests_has_no_buttons(session, bot_session_factory) -> None:
    await _seed(session)
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=FakeMessage(),
    )

    await reseller.pending_for_owner(update, SimpleNamespace(user_data={}))

    body, markup = update.effective_message.sent[-1]
    assert "Nessuna richiesta in attesa" in body
    assert markup is None


async def test_reseller_cannot_list_requests(session, bot_session_factory) -> None:
    await _seed(session)
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=777, first_name="Mario"),
        effective_chat=SimpleNamespace(id=777, type="private"),
        effective_message=FakeMessage(),
    )

    await reseller.pending_for_owner(update, SimpleNamespace(user_data={}))

    body, _markup = update.effective_message.sent[-1]
    assert "777" in body


async def test_approve_from_the_notification_creates_the_renewal(
    session, bot_session_factory
) -> None:
    """Il pulsante dell'avviso deve chiudere il giro: richiesta → rinnovo."""
    _reseller_row, line = await _seed(session)
    request = await _make_request(session, line)
    update = SimpleNamespace(
        callback_query=FakeQuery(f"rqa:{request.id}:2"),
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=SimpleNamespace(chat=SimpleNamespace(id=OWNER_ID, type="private")),
    )

    await reseller.approve(update, SimpleNamespace(user_data={}, bot=None))

    assert "Rinnovo approvato" in update.callback_query.edits[-1]
    async with bot_session_factory() as check:
        stored = await check.get(RenewalRequest, request.id)
        assert stored.status == RequestStatus.APPROVED
        renewals = list((await check.scalars(select(Renewal))).all())
        assert len(renewals) == 1
        renewed = await get_line(check, line.id)
        assert renewed.expires_on > date(2026, 11, 3)
