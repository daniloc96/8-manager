"""Un input in attesa non deve sopravvivere alla navigazione.

«Altro…» mette in attesa un numero (mesi o prezzo). Se l'utente annulla o apre
la scheda, quel numero non deve più trovare un flusso ad aspettarlo: senza
questo, un «12» digitato per cercare rinnovava la linea di dodici mesi.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import lines
from app.models import Customer, Line, Renewal, Service
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)
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
        self.answered = False
        self.edits: list[str] = []

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.sent: list[str] = []

    async def reply_html(self, text, **kwargs):
        self.sent.append(text)
        return self


def fake_update(data: str | None = None, text: str | None = None) -> SimpleNamespace:
    chat = SimpleNamespace(id=OWNER_ID, type="private")
    query = FakeQuery(data) if data is not None else None
    message = FakeMessage(text) if text is not None else None
    return SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=chat,
        effective_message=message,
    )


async def _make_line(session) -> Line:
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
    )
    session.add_all([customer, line])
    await session.commit()
    return line


async def test_card_clears_pending_months(session, bot_session_factory) -> None:
    line = await _make_line(session)
    context = SimpleNamespace(user_data={"awaiting": f"months:{line.id}"})

    # L'utente preme «Annulla» dal picker: torna alla scheda.
    await lines.card(fake_update(f"card:{line.id}"), context)
    assert "awaiting" not in context.user_data

    # Poi digita un numero per cercare altro: non deve essere un rinnovo.
    handled = await lines.handle_pending_text(fake_update(text="12"), context)

    assert handled is False
    async with bot_session_factory() as check:
        renewals = list((await check.scalars(__import__("sqlalchemy").select(Renewal))).all())
        assert renewals == []


async def test_card_clears_pending_price(session, bot_session_factory) -> None:
    line = await _make_line(session)
    context = SimpleNamespace(user_data={"awaiting": f"price:{line.id}"})

    await lines.card(fake_update(f"card:{line.id}"), context)

    assert "awaiting" not in context.user_data
    assert await lines.handle_pending_text(fake_update(text="9"), context) is False


async def test_pending_flow_still_works_without_navigation(session, bot_session_factory) -> None:
    """La correzione non deve rompere il flusso legittimo."""
    line = await _make_line(session)
    context = SimpleNamespace(user_data={"awaiting": f"months:{line.id}"})

    handled = await lines.handle_pending_text(fake_update(text="3"), context)

    assert handled is True
    async with bot_session_factory() as check:
        fresh = await check.get(Line, line.id)
        assert fresh is not None
        assert fresh.expires_on > date(2027, 1, 1)
