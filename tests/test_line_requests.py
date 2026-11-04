"""Richiesta di una nuova linea: wizard, conferma owner e blocco copia/incolla."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import group_menu, line_request, reseller
from app.models import (
    Line,
    NewLineRequest,
    PanelLine,
    Platform,
    RequestStatus,
    Reseller,
    Service,
    Setting,
)
from app.services.line_requests import (
    NewLineRequestError,
    approve_new_line_request,
    create_new_line_request,
    reject_new_line_request,
)

OWNER_ID = 42
RESELLER_TG = 777
PRESSER = 5551
GROUP_ID = -100999
PRIVATE = SimpleNamespace(id=RESELLER_TG, type="private", title=None)
GROUP = SimpleNamespace(id=GROUP_ID, type="supergroup", title="Gruppo Mario")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", str(OWNER_ID))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fake_sync(monkeypatch):
    """Nessun test deve uscire verso i pannelli veri."""

    async def no_sync() -> None:
        return None

    monkeypatch.setattr(line_request, "_sync_panels", no_sync)


@pytest.fixture
def bot_session_factory(engine, monkeypatch):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


class FakeQuery:
    def __init__(self, data: str, message_id: int = 555) -> None:
        self.data = data
        self.message = SimpleNamespace(message_id=message_id)
        self.edits: list[str] = []
        self.markup = None
        self.answered: list[str] = []

    async def answer(self, text=None, **kwargs):
        if text:
            self.answered.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
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


def callback_update(data: str, *, chat, user_id: int = PRESSER):
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(
            id=user_id, first_name="Anna", username="anna", full_name="Anna Bianchi"
        ),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


def message_update(text: str, *, chat, user_id: int = PRESSER):
    return SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(
            id=user_id, first_name="Anna", username="anna", full_name="Anna Bianchi"
        ),
        effective_chat=chat,
        effective_message=FakeMessage(text),
    )


def context(**kwargs):
    return SimpleNamespace(bot=FakeBot(), user_data={}, chat_data={}, **kwargs)


def _callbacks(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def _seed(session, *, with_group: bool = True):
    reseller_row = Reseller(
        name="Mario",
        name_key="mario",
        telegram_user_id=RESELLER_TG,
        group_chat_id=GROUP_ID if with_group else None,
        active=True,
        notify=True,
        default_monthly_price_cents=700,
    )
    session.add(reseller_row)
    await session.commit()
    return reseller_row


async def _request(
    session,
    reseller_row,
    *,
    name: str = "Mario Rossi",
    months: int = 3,
    service: Service = Service.LIVE_VOD,
    note: str | None = "Mario amico di Luca",
    today: date = date(2026, 11, 4),
) -> NewLineRequest:
    request = await create_new_line_request(
        session,
        reseller_id=reseller_row.id,
        requested_by=RESELLER_TG,
        name=name,
        months=months,
        service=service,
        note=note,
        today=today,
        origin_chat_id=PRIVATE.id,
        origin_message_id=9,
    )
    await session.commit()
    return request


async def _add_panels(
    factory,
    *,
    line_id: int,
    username: str,
    note: str | None,
    expires_on: date,
    service: Service = Service.LIVE_VOD,
) -> None:
    """Simula i dati che il sync porta dal pannello."""
    platforms = (
        [Platform.LIVE_HALLO, Platform.VOD_NEWVOD]
        if service is Service.LIVE_VOD
        else [Platform.LIVE_HALLO]
    )
    async with factory() as session:
        for platform in platforms:
            session.add(
                PanelLine(
                    line_id=line_id,
                    platform=platform,
                    username=username,
                    username_key=username,
                    password="Xy7k2p",
                    access_code=username,
                    expires_on=expires_on,
                    note=note,
                )
            )
        session.add(Setting(key="live_domain", value="http://live.test"))
        session.add(Setting(key="vod_domain", value="http://vod.test"))
        await session.commit()


# --- servizio ---------------------------------------------------------------


async def test_username_freezes_the_request_day(session) -> None:
    reseller_row = await _seed(session)
    request = await _request(session, reseller_row, name="Cafè Núñez", today=date(2027, 1, 9))

    assert request.username == "cafenunez090127"
    assert request.name == "Cafè Núñez"
    assert request.status is RequestStatus.PENDING


async def test_two_requests_with_the_same_name_get_distinct_ids(session) -> None:
    reseller_row = await _seed(session)
    first = await _request(session, reseller_row)
    second = await _request(session, reseller_row)

    assert first.username == "mariorossi041126"
    assert second.username == "mariorossi0411262"


async def test_request_rejects_out_of_range_months(session) -> None:
    reseller_row = await _seed(session)
    with pytest.raises(NewLineRequestError):
        await _request(session, reseller_row, months=99)


async def test_approval_creates_a_new_customer_and_line(session) -> None:
    reseller_row = await _seed(session)
    request = await _request(session, reseller_row)

    approved, customer, line, username = await approve_new_line_request(
        session,
        request_id=request.id,
        price_cents=700,
        approved_by=OWNER_ID,
        today=date(2026, 11, 10),
    )

    assert approved.status is RequestStatus.APPROVED
    assert approved.line_id == line.id
    assert approved.resolved_by_telegram_id == OWNER_ID
    assert customer.name == "Mario Rossi"
    assert customer.notes == "Mario amico di Luca"
    assert line.customer_id == customer.id
    assert line.service is Service.LIVE_VOD
    assert line.monthly_price_cents == 700
    assert line.reseller_id == reseller_row.id
    assert line.expires_on == date(2027, 2, 10)
    # L'ID resta quello del giorno della richiesta, non dell'approvazione.
    assert username == "mariorossi041126"


async def test_rejection_creates_nothing(session) -> None:
    reseller_row = await _seed(session)
    request = await _request(session, reseller_row)

    rejected = await reject_new_line_request(session, request_id=request.id, rejected_by=OWNER_ID)

    assert rejected.status is RequestStatus.REJECTED
    assert await session.scalar(select(Line)) is None


# --- flusso privato ---------------------------------------------------------


async def _run_private_wizard(context, *, name="Mario Rossi", months="3"):
    start = callback_update("menu:newline", chat=PRIVATE, user_id=RESELLER_TG)
    await line_request.start_private(start, context)
    assert context.user_data[line_request.STATE_KEY]["step"] == "name"

    typed_name = message_update(name, chat=PRIVATE, user_id=RESELLER_TG)
    assert await line_request.handle_text(typed_name, context) is True
    assert context.user_data[line_request.STATE_KEY]["step"] == "months"

    picked = callback_update(f"nlr:m:{months}", chat=PRIVATE, user_id=RESELLER_TG)
    await line_request.on_callback(picked, context)
    assert context.user_data[line_request.STATE_KEY]["step"] == "service"

    service = callback_update("nlr:s:live_vod", chat=PRIVATE, user_id=RESELLER_TG)
    await line_request.on_callback(service, context)
    assert context.user_data[line_request.STATE_KEY]["step"] == "note"

    typed_note = message_update("Mario amico di Luca", chat=PRIVATE, user_id=RESELLER_TG)
    assert await line_request.handle_text(typed_note, context) is True
    assert context.user_data[line_request.STATE_KEY]["step"] == "confirm"
    assert _callbacks(typed_note.effective_message.sent[-1][1]) == [
        "nlr:send",
        "nlr:cancel",
    ]

    sent = callback_update("nlr:send", chat=PRIVATE, user_id=RESELLER_TG)
    await line_request.on_callback(sent, context)
    return sent


async def test_private_wizard_creates_the_request_and_tells_the_owner(
    session, bot_session_factory
) -> None:
    await _seed(session, with_group=False)
    ctx = context()

    sent = await _run_private_wizard(ctx)

    assert "Richiesta inviata" in sent.callback_query.edits[-1]
    assert line_request.STATE_KEY not in ctx.user_data
    stored = await session.scalar(select(NewLineRequest))
    assert stored is not None
    assert stored.name == "Mario Rossi"
    assert stored.months == 3
    assert stored.service is Service.LIVE_VOD
    assert stored.note == "Mario amico di Luca"
    assert stored.origin_chat_id == PRIVATE.id

    owner_chat, owner_text, owner_markup = ctx.bot.sent[-1]
    assert owner_chat == OWNER_ID
    assert "Il rivenditore Mario chiede una nuova linea" in owner_text
    assert "Mario Rossi" in owner_text
    assert "mariorossi041126" in owner_text
    assert _callbacks(owner_markup) == [f"nla:{stored.id}", f"nlx:{stored.id}"]


async def test_owner_cannot_start_the_reseller_wizard(session, bot_session_factory) -> None:
    await _seed(session, with_group=False)
    ctx = context()
    update = callback_update("menu:newline", chat=SimpleNamespace(id=OWNER_ID, type="private"))

    await line_request.start_private(update, ctx)

    assert "rivenditore dal proprio account" in update.callback_query.edits[-1]
    assert line_request.STATE_KEY not in ctx.user_data


# --- conferma owner ---------------------------------------------------------


async def _confirm_with_price(ctx, request, owner_chat):
    ask = callback_update(f"nla:{request.id}", chat=owner_chat, user_id=OWNER_ID)
    await line_request.on_owner_callback(ask, ctx)
    assert "Prezzo mensile" in ask.callback_query.edits[-1]
    callbacks = _callbacks(ask.callback_query.markup)
    assert f"nlp:{request.id}:default" in callbacks
    assert f"nlp:{request.id}:other" in callbacks

    confirm = callback_update(f"nlp:{request.id}:default", chat=owner_chat, user_id=OWNER_ID)
    await line_request.on_owner_callback(confirm, ctx)
    return confirm


async def test_confirmation_without_panel_data_offers_retry(session, bot_session_factory) -> None:
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row, months=3)
    ctx = context()
    owner_chat = SimpleNamespace(id=OWNER_ID, type="private")

    confirm = await _confirm_with_price(ctx, request, owner_chat)

    body = confirm.callback_query.edits[-1]
    assert "Linea creata, ma il pannello non è ancora completo" in body
    assert "Non trovo: Live, VOD" in body
    assert "<pre>" in body
    assert "ID: mariorossi041126" in body
    assert "DURATA: 3 mesi" in body
    assert "NOTE: Mario amico di Luca" in body
    assert "PASSWORD:" not in body
    assert f"nls:{request.id}" in _callbacks(confirm.callback_query.markup)

    async with bot_session_factory() as check:
        stored = await check.get(NewLineRequest, request.id)
        assert stored.status is RequestStatus.APPROVED
        line = await check.get(Line, stored.line_id)
        assert line is not None and line.monthly_price_cents == 700
        assert stored.announced_at is None

    # Niente codici, niente messaggio al gruppo.
    assert ctx.bot.sent == []


async def test_retry_after_panel_creation_sends_codes_and_links(
    session, bot_session_factory
) -> None:
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row, months=3)
    ctx = context()
    owner_chat = SimpleNamespace(id=OWNER_ID, type="private")
    await _confirm_with_price(ctx, request, owner_chat)

    async with bot_session_factory() as check:
        stored = await check.get(NewLineRequest, request.id)
        line_id = stored.line_id
    await _add_panels(
        bot_session_factory,
        line_id=line_id,
        username=request.username,
        note=request.note,
        expires_on=date(2027, 2, 4),
    )

    retry = callback_update(f"nls:{request.id}", chat=owner_chat, user_id=OWNER_ID)
    await line_request.on_owner_callback(retry, ctx)

    body = retry.callback_query.edits[-1]
    assert "Nuova linea attiva" in body
    assert "username: <code>mariorossi041126</code>" in body
    assert "password: <code>Xy7k2p</code>" in body
    assert (
        "link Live: <code>http://live.test/get.php?username=mariorossi041126"
        "&amp;password=Xy7k2p&amp;type=m3u_plus&amp;output=mpegts</code>"
    ) in body
    assert "link VOD: <code>http://vod.test/get.php" in body

    assert ctx.bot.sent[-1][0] == PRIVATE.id
    assert "link Live" in ctx.bot.sent[-1][1]
    async with bot_session_factory() as check:
        stored = await check.get(NewLineRequest, request.id)
        assert stored.announced_at is not None


async def test_confirmation_with_panel_data_sends_codes_immediately(
    session, bot_session_factory, monkeypatch
) -> None:
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row, months=3)
    ctx = context()
    owner_chat = SimpleNamespace(id=OWNER_ID, type="private")

    async def fake_sync() -> None:
        async with bot_session_factory() as check:
            stored = await check.get(NewLineRequest, request.id)
            line_id = stored.line_id
        await _add_panels(
            bot_session_factory,
            line_id=line_id,
            username=request.username,
            note=request.note,
            expires_on=date(2027, 2, 4),
        )

    monkeypatch.setattr(line_request, "_sync_panels", fake_sync)

    confirm = await _confirm_with_price(ctx, request, owner_chat)

    body = confirm.callback_query.edits[-1]
    assert "Nuova linea attiva" in body
    assert "username: <code>mariorossi041126</code>" in body
    assert "link VOD" in body
    assert f"nls:{request.id}" not in _callbacks(confirm.callback_query.markup)
    assert "link Live" in ctx.bot.sent[-1][1]


async def test_owner_rejection_is_announced(session, bot_session_factory) -> None:
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row)
    ctx = context()
    owner_chat = SimpleNamespace(id=OWNER_ID, type="private")

    reject = callback_update(f"nlx:{request.id}", chat=owner_chat, user_id=OWNER_ID)
    await line_request.on_owner_callback(reject, ctx)

    assert "Richiesta rifiutata" in reject.callback_query.edits[-1]
    async with bot_session_factory() as check:
        stored = await check.get(NewLineRequest, request.id)
        assert stored.status is RequestStatus.REJECTED
    assert ctx.bot.sent[-1][0] == PRIVATE.id

    # Doppio tap: la seconda volta non deve rifiutare di nuovo.
    again = callback_update(f"nlx:{request.id}", chat=owner_chat, user_id=OWNER_ID)
    await line_request.on_owner_callback(again, ctx)
    assert "Non rifiutata" in again.callback_query.edits[-1]


async def test_reseller_cannot_confirm(session, bot_session_factory) -> None:
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row)
    ctx = context()

    update = callback_update(
        f"nla:{request.id}",
        chat=SimpleNamespace(id=RESELLER_TG, type="private"),
        user_id=RESELLER_TG,
    )
    await line_request.on_owner_callback(update, ctx)

    assert "Solo l'owner" in update.callback_query.answered[-1]
    stored = await session.get(NewLineRequest, request.id)
    assert stored.status is RequestStatus.PENDING


async def test_owner_price_typed_by_hand(session, bot_session_factory) -> None:
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row)
    ctx = context()
    ctx.user_data["awaiting"] = f"nlprice:{request.id}"
    owner_chat = SimpleNamespace(id=OWNER_ID, type="private")
    update = message_update("7,50", chat=owner_chat, user_id=OWNER_ID)

    handled = await line_request.handle_owner_price_text(update, ctx, ctx.user_data["awaiting"])

    assert handled is True
    async with bot_session_factory() as check:
        stored = await check.get(NewLineRequest, request.id)
        assert stored.status is RequestStatus.APPROVED
        line = await check.get(Line, stored.line_id)
        assert line.monthly_price_cents == 750


# --- gruppo -----------------------------------------------------------------


async def test_group_menu_starts_the_wizard_in_the_chat(session, bot_session_factory) -> None:
    await _seed(session)
    ctx = context()
    update = callback_update("gmenu:newline", chat=GROUP)

    await group_menu.on_callback(update, ctx)

    assert "Come si chiama il cliente" in update.callback_query.edits[-1]
    assert ctx.chat_data[line_request.STATE_KEY]["step"] == "name"
    assert ctx.chat_data[line_request.STATE_KEY]["origin_chat_id"] == GROUP_ID


async def test_group_wizard_keeps_the_origin_and_tells_the_owner(
    session, bot_session_factory
) -> None:
    await _seed(session)
    ctx = context()
    start = callback_update("gmenu:newline", chat=GROUP)
    await group_menu.on_callback(start, ctx)

    typed_name = message_update("Anna Bianchi", chat=GROUP)
    assert await group_menu.handle_text(typed_name, ctx) is True
    assert ctx.chat_data[line_request.STATE_KEY]["step"] == "months"

    await line_request.on_callback(callback_update("nlr:m:2", chat=GROUP), ctx)
    await line_request.on_callback(callback_update("nlr:s:live", chat=GROUP), ctx)
    await line_request.on_callback(callback_update("nlr:n:skip", chat=GROUP), ctx)
    sent = callback_update("nlr:send", chat=GROUP)
    await line_request.on_callback(sent, ctx)

    stored = await session.scalar(
        select(NewLineRequest).where(NewLineRequest.name == "Anna Bianchi")
    )
    assert stored is not None
    assert stored.origin_chat_id == GROUP_ID
    assert stored.service is Service.LIVE
    assert stored.note is None
    assert "Richiesta inviata" in sent.callback_query.edits[-1]
    owner_messages = [text for chat_id, text, _m in ctx.bot.sent if chat_id == OWNER_ID]
    assert owner_messages and "chiede una nuova linea" in owner_messages[0]


async def test_sync_flush_sends_pending_codes_once(session, bot_session_factory) -> None:
    """Anche un `/sync` manuale chiude il giro di una richiesta già confermata."""
    reseller_row = await _seed(session, with_group=False)
    request = await _request(session, reseller_row)
    _approved, _customer, line, _username = await approve_new_line_request(
        session,
        request_id=request.id,
        price_cents=700,
        approved_by=OWNER_ID,
        today=date(2026, 11, 4),
    )
    await session.commit()
    await _add_panels(
        bot_session_factory,
        line_id=line.id,
        username=request.username,
        note=request.note,
        expires_on=date(2027, 2, 4),
    )
    ctx = context()

    assert await line_request.announce_ready_requests(ctx) == 1
    assert ctx.bot.sent[-1][0] == PRIVATE.id
    assert "link Live" in ctx.bot.sent[-1][1]
    assert await line_request.announce_ready_requests(ctx) == 0


# --- elenco owner -----------------------------------------------------------


async def test_richieste_lists_new_lines_with_buttons(session, bot_session_factory) -> None:
    reseller_row = await _seed(session)
    request = await _request(session, reseller_row)
    message = FakeMessage()
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=message,
    )

    await reseller.pending_for_owner(update, context())

    body, markup = message.sent[-1]
    assert "Nuove linee" in body
    assert "Mario Rossi" in body
    assert "3 mesi" in body
    assert _callbacks(markup) == [f"nla:{request.id}", f"nlx:{request.id}"]


def test_new_line_callbacks_are_registered(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", str(OWNER_ID))
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import build_application

        application = build_application()
        patterns = {
            handler.pattern.pattern
            for handler in application.handlers[0]
            if getattr(handler, "pattern", None) is not None
        }
        assert "^nlr:" in patterns
        assert "^(nla|nlp|nlx|nls):" in patterns
    finally:
        get_settings.cache_clear()
