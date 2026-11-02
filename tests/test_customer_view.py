from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import customer as customer_bot
from app.bot import ui
from app.models import Customer, Line, Reseller, Service
from app.services.queries import customer_overview
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

    async def answer(self, *args, **kwargs) -> None:
        return None

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


async def _seed(session):
    reseller = Reseller(name="Luca", name_key="luca", telegram_user_id=777)
    mario = Customer(name="Mario Caterino", search_key=customer_search_key("Mario Caterino"))
    session.add_all([reseller, mario])
    await session.flush()
    lines = [
        Line(
            customer=mario,
            service=Service.LIVE_VOD,
            monthly_price_cents=500,
            started_on=date(2025, 1, 1),
            expires_on=date(2026, 11, 13),
            anchor_day=1,
            label="Linea 1",
        ),
        Line(
            customer=mario,
            service=Service.LIVE,
            monthly_price_cents=600,
            started_on=date(2025, 1, 1),
            expires_on=date(2026, 12, 1),
            anchor_day=1,
            label="Linea 2",
            reseller_id=reseller.id,
        ),
        Line(
            customer=mario,
            service=Service.LIVE,
            monthly_price_cents=700,
            started_on=date(2025, 1, 1),
            expires_on=date(2027, 1, 1),
            anchor_day=1,
            label="Linea 3",
        ),
    ]
    session.add_all(lines)
    await session.commit()
    return mario, reseller, lines


async def test_overview_totals_every_active_line(session) -> None:
    mario, _reseller, _lines = await _seed(session)

    overview = await customer_overview(session, mario.id)

    assert overview is not None
    assert overview.active_lines == 3
    assert overview.monthly_cents == 1800
    assert overview.hidden_lines == 0
    assert [line.label for line in overview.lines] == ["Linea 1", "Linea 2", "Linea 3"]


async def test_overview_filters_for_reseller(session) -> None:
    mario, reseller, _lines = await _seed(session)

    overview = await customer_overview(session, mario.id, reseller_id=reseller.id)

    assert overview is not None
    assert overview.active_lines == 1
    assert overview.monthly_cents == 600
    assert overview.hidden_lines == 2


async def test_overview_skips_deleted_lines_and_customers(session) -> None:
    mario, _reseller, lines = await _seed(session)
    lines[0].deleted_at = datetime.now(UTC)
    await session.commit()

    overview = await customer_overview(session, mario.id)
    assert overview.active_lines == 2
    assert overview.monthly_cents == 1300

    mario.deleted_at = datetime.now(UTC)
    await session.commit()
    assert await customer_overview(session, mario.id) is None


async def test_card_shows_total_and_each_line(session) -> None:
    mario, _reseller, _lines = await _seed(session)
    overview = await customer_overview(session, mario.id)

    card = ui.customer_card(overview, TODAY)

    assert "Mario Caterino" in card
    assert "3 linee attive" in card
    assert "18,00 €/mese" in card
    assert "Linea 1" in card and "Linea 3" in card


async def test_card_note_about_hidden_lines(session) -> None:
    mario, reseller, _lines = await _seed(session)
    overview = await customer_overview(session, mario.id, reseller_id=reseller.id)

    card = ui.customer_card(overview, TODAY)

    assert "2 linee non visibili a te" in card


async def test_results_keyboard_offers_customer_for_multiple_lines(session) -> None:
    _mario, _reseller, lines = await _seed(session)

    markup = ui.results_keyboard(lines, TODAY)
    data = [b.callback_data for row in markup.inline_keyboard for b in row]

    assert f"cust:{lines[0].customer_id}" in data
    assert f"card:{lines[0].id}" in data


async def test_callback_owner_sees_all_lines(session, bot_session_factory) -> None:
    mario, _reseller, _lines = await _seed(session)
    update, query = _update(f"cust:{mario.id}")

    await customer_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "3 linee attive" in query.text
    data = [b.callback_data for row in query.markup.inline_keyboard for b in row]
    assert len([item for item in data if item.startswith("card:")]) == 3


async def test_callback_reseller_sees_only_own_lines(session, bot_session_factory) -> None:
    mario, _reseller, _lines = await _seed(session)
    update, query = _update(f"cust:{mario.id}", user_id=777)

    await customer_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text is not None
    assert "1 linea attiva" in query.text
    assert "Linea 2" in query.text
    assert "Linea 1" not in query.text


async def test_callback_rejects_unknown_user(session, bot_session_factory) -> None:
    mario, _reseller, _lines = await _seed(session)
    update, query = _update(f"cust:{mario.id}", user_id=12345)

    await customer_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.text == "Non sei autorizzato."


async def test_reseller_does_not_see_other_lines_count(session) -> None:
    """Al rivenditore non si dice quante linee dello stesso cliente esistono."""
    from app.bot import ui
    from app.services.queries import customer_overview

    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    mine_reseller = Reseller(name="Mio", name_key="mio", active=True)
    other = Reseller(name="Altro", name_key="altro", active=True)
    session.add_all([customer, mine_reseller, other])
    await session.commit()

    mine = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        reseller_id=mine_reseller.id,
    )
    # Una seconda linea dello stesso cliente, di un altro rivenditore.
    theirs = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=700,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        reseller_id=other.id,
    )
    session.add_all([mine, theirs])
    await session.commit()

    overview = await customer_overview(session, customer.id, reseller_id=mine_reseller.id)
    assert overview is not None

    reseller_view = ui.customer_card(overview, date(2026, 11, 2), show_hidden=False)
    owner_view = ui.customer_card(overview, date(2026, 11, 2), show_hidden=True)

    assert "non visibil" not in reseller_view
    assert "non visibil" in owner_view
