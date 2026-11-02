from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import menu
from app.models import Customer, Line, Service
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
    """Fa usare all'handler la stessa sessione/engine dei test."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


class FakeQuery:
    def __init__(self) -> None:
        self.data = "menu:income"
        self.text: str | None = None
        self.markup = None

    async def answer(self, *args, **kwargs) -> None:
        return None

    async def edit_message_text(self, text, **kwargs) -> None:
        self.text = text
        self.markup = kwargs.get("reply_markup")


def _update(user_id: int = OWNER_ID) -> tuple[SimpleNamespace, FakeQuery]:
    query = FakeQuery()
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=None,
    )
    return update, query


async def _seed(session) -> None:
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    other = Customer(name="Anna Bianchi", search_key=customer_search_key("Anna Bianchi"))
    session.add_all([customer, other])
    await session.flush()
    session.add_all(
        [
            Line(
                customer=customer,
                service=Service.LIVE_VOD,
                monthly_price_cents=500,
                started_on=date(2025, 1, 1),
                expires_on=date(2026, 11, 13),
                anchor_day=1,
            ),
            Line(
                customer=other,
                service=Service.LIVE,
                monthly_price_cents=700,
                started_on=date(2025, 1, 1),
                expires_on=date(2026, 12, 1),
                anchor_day=1,
            ),
        ]
    )
    await session.commit()


async def test_income_shows_portfolio_line(session, bot_session_factory) -> None:
    await _seed(session)
    update, query = _update()

    await menu.show_income(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Parco" in query.text
    assert "2 linee attive" in query.text
    assert "12,00 €/mese" in query.text
    # Nessun rinnovo nel mese: il parco c'è comunque.
    assert "Nessun rinnovo registrato questo mese." in query.text


async def test_income_portfolio_ignores_deleted_lines(session, bot_session_factory) -> None:
    from datetime import UTC, datetime

    await _seed(session)
    line = await session.get(Line, 2)
    line.deleted_at = datetime.now(UTC)
    await session.commit()
    update, query = _update()

    await menu.show_income(update, SimpleNamespace(user_data={}, bot=None))

    assert "1 linee attive" in query.text
    assert "5,00 €/mese" in query.text


async def test_income_rejects_non_owner(session, bot_session_factory) -> None:
    await _seed(session)
    update, query = _update(user_id=999)

    await menu.show_income(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "Non sei autorizzato" in query.text
