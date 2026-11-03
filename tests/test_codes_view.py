"""Vista «Codici»: cosa il bot ha davvero salvato per ogni pannello."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.bot import codes, ui
from app.models import Customer, Line, PanelLine, Platform, Reseller, Service
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
    from sqlalchemy.ext.asyncio import async_sessionmaker

    import app.db as db_module

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.alerts: list[str] = []
        self.edits: list[str] = []

    async def answer(self, text=None, **kwargs):
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


def callback_update(data: str, *, user_id: int = OWNER_ID) -> SimpleNamespace:
    chat = SimpleNamespace(id=user_id, type="private")
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(id=user_id, first_name="Danilo"),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


async def _seed(session, *, reseller_id: int | None = None):
    customer = Customer(name="Mario Caterino", search_key=customer_search_key("Mario Caterino"))
    session.add(customer)
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        reseller_id=reseller_id,
    )
    session.add(line)
    await session.flush()

    session.add_all(
        [
            PanelLine(
                platform=Platform.LIVE_HALLO,
                line_id=line.id,
                external_id="5512",
                username="mario2026",
                username_key="mario2026",
                password="segreta123",
                access_code="ACC-77",
                expires_on=date(2027, 1, 1),
                first_seen_at=datetime.now(),
            ),
            PanelLine(
                platform=Platform.LIVE_8TVPLUS,
                line_id=line.id,
                external_id="880",
                username="mario2026",
                username_key="mario2026-8tv",
                access_code="mario2026",
                first_seen_at=datetime.now(),
            ),
        ]
    )
    await session.flush()
    return customer, line


async def test_owner_sees_every_saved_code(session, bot_session_factory) -> None:
    customer, _line = await _seed(session)
    await session.commit()

    update = callback_update(f"codes:{customer.id}")
    await codes.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    assert "mario2026" in body
    assert "segreta123" in body
    assert "ACC-77" in body
    assert "5512" in body and "880" in body


async def test_eightplus_without_password_says_so(session, bot_session_factory) -> None:
    customer, _line = await _seed(session)
    await session.commit()

    update = callback_update(f"codes:{customer.id}")
    await codes.on_callback(update, SimpleNamespace(user_data={}))

    body = update.callback_query.edits[-1]
    # Per 8TV+ non c'è password: si accede con il codice, che è lo username.
    assert "nessuna password: sul pannello si accede con il codice" in body
    assert "codice: coincide con lo username" in body


async def test_reseller_cannot_see_codes(session, bot_session_factory) -> None:
    _customer, line = await _seed(session)
    reseller = Reseller(name="Luca", name_key="luca", telegram_user_id=777, active=True)
    session.add(reseller)
    await session.flush()
    line.reseller_id = reseller.id
    await session.commit()

    update = callback_update(f"codes:{line.customer_id}", user_id=777)
    await codes.on_callback(update, SimpleNamespace(user_data={}))

    assert update.callback_query.edits == []
    assert update.callback_query.alerts


async def test_line_without_panel_rows_is_explained(session, bot_session_factory) -> None:
    customer = Customer(name="Anna Neri", search_key=customer_search_key("Anna Neri"))
    session.add(customer)
    await session.flush()
    session.add(
        Line(
            customer=customer,
            service=Service.LIVE,
            monthly_price_cents=500,
            started_on=date(2025, 1, 1),
            expires_on=date(2027, 1, 1),
            anchor_day=1,
        )
    )
    await session.commit()

    update = callback_update(f"codes:{customer.id}")
    await codes.on_callback(update, SimpleNamespace(user_data={}))

    assert "arriva con il /sync" in update.callback_query.edits[-1]


async def test_buttons_appear_for_owner_only(session) -> None:
    customer, line = await _seed(session)
    await session.commit()

    from app.services.queries import customer_overview

    overview = await customer_overview(session, customer.id)
    assert overview is not None

    def datas(markup):
        return [b.callback_data for row in markup.inline_keyboard for b in row]

    assert f"codes:{customer.id}" in datas(ui.customer_keyboard(overview, is_owner=True))
    assert f"codes:{customer.id}" not in datas(ui.customer_keyboard(overview))
    assert f"codes:{customer.id}" in datas(ui.line_keyboard(line, is_owner=True)), (
        "la scheda linea porta alla stessa vista"
    )
