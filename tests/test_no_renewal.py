from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import flagged as flagged_bot
from app.bot import ui
from app.jobs.reminders import build_reminder
from app.models import AuditLog, Customer, Line, Service
from app.services.no_renewal import NoRenewalError, set_no_renewal
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


def _update(data: str, user_id: int = OWNER_ID):
    query = FakeQuery(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=None,
    )
    return update, query


async def _line(session, *, expires_on: date = TODAY) -> Line:
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=expires_on,
        anchor_day=1,
    )
    session.add_all([customer, line])
    await session.commit()
    return line


async def _flag(engine, line_id: int, flagged: bool = True) -> None:
    """Marca la linea committando sullo stesso engine del test.

    `bot_session_factory` usa un engine separato: senza un commit reale il
    reminder, che apre la propria sessione, non vedrebbe la marcatura.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as own:
        await set_no_renewal(own, line_id, flagged)
        await own.commit()


async def test_flag_toggles_and_writes_audit(session) -> None:
    line = await _line(session)

    flagged, changed = await set_no_renewal(session, line.id, True, actor_telegram_id=OWNER_ID)

    assert changed is True
    assert flagged.no_renewal is True
    assert flagged.no_renewal_at is not None

    entry = await session.scalar(select(AuditLog).where(AuditLog.action == "line_no_renewal_set"))
    assert entry is not None and entry.payload["new"] is True

    cleared, changed = await set_no_renewal(session, line.id, False)
    assert changed is True
    assert cleared.no_renewal is False
    assert cleared.no_renewal_at is None


async def test_flag_is_idempotent(session) -> None:
    line = await _line(session)
    await set_no_renewal(session, line.id, True)

    _line_again, changed = await set_no_renewal(session, line.id, True)

    assert changed is False


async def test_flag_on_missing_line_raises(session) -> None:
    with pytest.raises(NoRenewalError):
        await set_no_renewal(session, 9999, True)


async def test_card_shows_flag(session, engine) -> None:
    line = await _line(session)
    line_id = line.id
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as fresh:
        before = await fresh.get(Line, line_id)
        assert before is not None
        assert "Da non rinnovare" not in ui.line_card(before, TODAY)

    await _flag(engine, line_id)

    async with factory() as fresh:
        after = await fresh.get(Line, line_id)
        assert after is not None
        assert "Da non rinnovare" in ui.line_card(after, TODAY)


async def test_reminder_lists_flagged_lines(session, engine) -> None:
    line = await _line(session)
    line_id = line.id
    await _flag(engine, line_id)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    payload = await build_reminder(TODAY, chat_id=OWNER_ID, factory=factory)

    assert payload is not None
    body, markup = payload
    assert "Da non rinnovare" in body
    # Il pulsante ora annulla la marcatura, non la imposta.
    assert any(
        b.callback_data == f"nr:{line_id}:clear" for row in markup.inline_keyboard for b in row
    )


async def test_reminder_alone_for_flagged_line_still_sends(session, engine) -> None:
    """Una linea marcata resta nel reminder anche se non è in scadenza."""
    line = await _line(session, expires_on=date(2027, 6, 1))
    await _flag(engine, line.id)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    payload = await build_reminder(TODAY, chat_id=OWNER_ID, factory=factory)

    assert payload is not None
    assert "Da non rinnovare" in payload[0]


async def test_callback_set_shows_confirmation_first(session, bot_session_factory) -> None:
    line = await _line(session)
    update, query = _update(f"nr:{line.id}:set")

    await flagged_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Non rinnovare" in query.text
    data = [b.callback_data for row in query.markup.inline_keyboard for b in row]
    assert f"nr:{line.id}:confirm" in data

    async with bot_session_factory() as check:
        assert (await check.get(Line, line.id)).no_renewal is False


async def test_callback_confirm_sets_flag(session, bot_session_factory) -> None:
    line = await _line(session)
    update, query = _update(f"nr:{line.id}:confirm")

    await flagged_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "da non rinnovare" in query.text.lower()
    async with bot_session_factory() as check:
        assert (await check.get(Line, line.id)).no_renewal is True


async def test_callback_rejects_non_owner(session, bot_session_factory) -> None:
    line = await _line(session)
    update, query = _update(f"nr:{line.id}:confirm", user_id=999)

    await flagged_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.alerts and "owner" in query.alerts[0]
    async with bot_session_factory() as check:
        assert (await check.get(Line, line.id)).no_renewal is False


async def test_renewal_clears_the_flag(session, engine) -> None:
    """Rinnovare la linea toglie la marcatura, senza passare dal pulsante."""
    from app.models import AuditLog
    from app.models.enums import RenewalSource
    from app.services.lines import renew_line

    line = await _line(session)
    line_id = line.id
    await _flag(engine, line_id)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as own:
        fresh = await own.get(Line, line_id)
        assert fresh is not None
        assert fresh.no_renewal is True

        await renew_line(own, fresh, months=3, today=TODAY, source=RenewalSource.CARD)
        await own.commit()

    async with factory() as own:
        after = await own.get(Line, line_id)
        assert after is not None
        assert after.no_renewal is False
        assert after.no_renewal_at is None
        audit = await own.scalar(
            select(AuditLog).where(
                AuditLog.entity == "line",
                AuditLog.entity_id == line_id,
                AuditLog.action == "line_no_renewal_clear",
            )
        )
        assert audit is not None
        assert audit.payload["reason"] == "renewed"


async def test_flagged_line_far_from_expiry_has_a_button(session, engine) -> None:
    """Una linea marcata lontana dalla scadenza deve restare smarcabile."""
    line = await _line(session, expires_on=date(2027, 6, 1))
    line_id = line.id
    await _flag(engine, line_id)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    built = await build_reminder(TODAY, chat_id=OWNER_ID, factory=factory)

    assert built is not None
    _body, markup = built
    datas = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert f"nr:{line_id}:clear" in datas
