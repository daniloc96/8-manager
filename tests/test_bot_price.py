from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.bot import lines
from app.db import get_session_factory
from app.models import Customer, Line, Service
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.sent: list[str] = []

    async def reply_html(self, text, **kwargs):
        self.sent.append(text)
        return self


@pytest.fixture
def bot_session_factory(engine, session, monkeypatch):
    """Fa usare all'handler la stessa sessione/engine dei test."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.db as db_module

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


async def _make_line(session) -> Line:
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    line = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
    )
    session.add_all([customer, line])
    await session.commit()
    return line


async def test_price_change_from_telegram_message(session, bot_session_factory) -> None:
    line = await _make_line(session)
    message = FakeMessage("6,50")
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(type="private"),
    )
    context = SimpleNamespace(user_data={"awaiting": f"price:{line.id}"})

    handled = await lines.handle_pending_text(update, context)

    assert handled is True
    assert message.sent
    assert "6,50 €" in message.sent[0]

    async with bot_session_factory() as check:
        refreshed = await check.get(Line, line.id)
        assert refreshed.monthly_price_cents == 650


async def test_invalid_price_is_rejected_and_state_kept(session, bot_session_factory) -> None:
    line = await _make_line(session)
    message = FakeMessage("due euro")
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(type="private"),
    )
    context = SimpleNamespace(user_data={"awaiting": f"price:{line.id}"})

    assert await lines.handle_pending_text(update, context) is True
    assert "non valido" in message.sent[0]
    assert context.user_data["awaiting"] == f"price:{line.id}"

    async with bot_session_factory() as check:
        refreshed = await check.get(Line, line.id)
        assert refreshed.monthly_price_cents == 500


def test_get_session_factory_is_used_by_handlers() -> None:
    """Guardia: gli handler leggono la factory globale, non una engine propria."""
    assert get_session_factory is not None
