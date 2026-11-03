"""Anomalie del sync: dal riepilogo alla singola linea, con l'azione giusta."""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import app.db as db_module
from app.bot import reviews as bot_reviews
from app.models import (
    AuditLog,
    Customer,
    Line,
    Platform,
    Service,
)
from app.panels.sync import PlatformResult, ReviewItem, SyncReport
from app.services.review_exceptions import ReviewExceptionError
from app.services.reviews import (
    accept_local,
    align_to_panel,
    describe,
    open_issue,
    open_issues,
    store_reviews,
)

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
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


async def _seed_line(session, *, local: date, panel: date | None = None) -> Line:
    customer = Customer(name="Mario Rossi", search_key="mario rossi")
    session.add(customer)
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=local,
        anchor_day=1,
    )
    session.add(line)
    await session.commit()
    return line


def _report(*items: ReviewItem) -> SyncReport:
    report = SyncReport(started_at=datetime.now(ZoneInfo("Europe/Rome")))
    result = PlatformResult(platform=Platform.LIVE_8TVPLUS)
    result.review = list(items)
    report.results = [result]
    return report


def _local_ahead(line_id: int, local: date, panel: date) -> ReviewItem:
    return ReviewItem(
        platform=Platform.LIVE_8TVPLUS,
        username="mario",
        reason=f"data locale {local:%d/%m/%Y} più avanti del pannello ({panel:%d/%m/%Y})",
        line_id=line_id,
        issue="local_ahead",
        local_expires=local,
        panel_expires=panel,
    )


async def test_sync_stores_the_anomaly_with_the_line(session) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20), panel=date(2026, 11, 20))

    stored = await store_reviews(
        session, _report(_local_ahead(line.id, line.expires_on, date(2026, 11, 20)))
    )
    await session.commit()

    record = await open_issue(session, line_id=line.id)
    assert stored == 1
    assert record is not None
    assert record.issue == "local_ahead"
    assert record.local_expires == date(2026, 12, 20)
    assert record.panel_expires == date(2026, 11, 20)


async def test_reviews_without_a_line_are_not_stored(session) -> None:
    """Una riga doppia nel pannello non ha una linea: resta nel messaggio."""
    await _seed_line(session, local=date(2026, 12, 20))

    stored = await store_reviews(
        session,
        _report(
            ReviewItem(
                platform=Platform.LIVE_8TVPLUS,
                username="doppio",
                reason="username duplicato nel pannello",
            )
        ),
    )
    await session.commit()

    assert stored == 0
    assert await open_issues(session) == []


async def test_second_sync_replaces_the_previous_open_anomalies(session) -> None:
    first = await _seed_line(session, local=date(2026, 12, 20))
    second = await _seed_line(session, local=date(2027, 1, 15))

    await store_reviews(
        session, _report(_local_ahead(first.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()
    await store_reviews(
        session, _report(_local_ahead(second.id, date(2027, 1, 15), date(2026, 12, 15)))
    )
    await session.commit()

    open_now = await open_issues(session)
    assert [line.id for _record, line in open_now] == [second.id]


async def test_align_copies_the_panel_date(session) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20), panel=date(2026, 11, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()

    updated, previous, target = await align_to_panel(
        session, line_id=line.id, actor_telegram_id=OWNER_ID
    )
    await session.commit()

    assert previous == date(2026, 12, 20)
    assert target == date(2026, 11, 20)
    assert updated.expires_on == date(2026, 11, 20)
    assert await open_issue(session, line_id=line.id) is None
    actions = list((await session.scalars(select(AuditLog.action))).all())
    assert "review_align" in actions


async def test_accept_keeps_the_local_date_until_it_expires(session) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()

    _line, local = await accept_local(session, line_id=line.id, actor_telegram_id=OWNER_ID)
    await session.commit()

    assert local == date(2026, 12, 20)
    reloaded = await session.get(Line, line.id)
    assert reloaded.expires_on == date(2026, 12, 20), "il locale resta quello"
    record = await open_issue(session, line_id=line.id)
    assert record is not None
    assert record.issue == "expiry_diff"
    assert record.review_on == date(2026, 12, 20), "l'eccezione scade col locale"


async def test_accepting_without_an_anomaly_is_reported(session) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    with pytest.raises(ReviewExceptionError):
        await accept_local(session, line_id=line.id, actor_telegram_id=OWNER_ID)


async def test_describe_shows_both_dates(session) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()

    record = await open_issue(session, line_id=line.id)
    text = describe(record)
    assert "locale 20/12/2026" in text
    assert "pannello 20/11/2026" in text


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.edits: list[tuple[str, object]] = []
        self.markup = None
        self.message = SimpleNamespace(message_id=1)
        self.alerts: list[str] = []

    async def answer(self, text=None, **kwargs):
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs.get("reply_markup")))
        self.markup = kwargs.get("reply_markup")
        return self


class FakeMessage:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    async def reply_html(self, text, **kwargs):
        self.sent.append((text, kwargs.get("reply_markup")))
        return self


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_card_offers_align_and_keep(session, bot_session_factory) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()

    query = FakeQuery(f"rv:{line.id}")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=SimpleNamespace(chat=SimpleNamespace(id=OWNER_ID, type="private")),
    )
    await bot_reviews.on_callback(update, SimpleNamespace(user_data={}))

    body, markup = query.edits[-1]
    assert "Scadenza locale" in body
    assert "20/12/2026" in body
    assert "20/11/2026" in body
    callbacks = _callbacks(markup)
    assert f"rv:{line.id}:align" in callbacks
    assert f"rv:{line.id}:accept" in callbacks


async def test_no_anomaly_card_says_so(session, bot_session_factory) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    query = FakeQuery(f"rv:{line.id}")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=OWNER_ID),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=SimpleNamespace(chat=SimpleNamespace(id=OWNER_ID, type="private")),
    )

    await bot_reviews.on_callback(update, SimpleNamespace(user_data={}))

    body, _markup = query.edits[-1]
    assert "Nessuna anomalia" in body


async def test_align_from_the_card_writes_the_panel_date(session, bot_session_factory) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()

    query = FakeQuery(f"rv:{line.id}:align")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=OWNER_ID),
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        effective_message=SimpleNamespace(chat=SimpleNamespace(id=OWNER_ID, type="private")),
    )
    await bot_reviews.on_callback(update, SimpleNamespace(user_data={}))

    body, _markup = query.edits[-1]
    assert "Data allineata" in body
    assert "20/11/2026" in body
    async with bot_session_factory() as check:
        stored = await check.get(Line, line.id)
        assert stored.expires_on == date(2026, 11, 20)


async def test_reseller_cannot_touch_anomalies(session, bot_session_factory) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()

    query = FakeQuery(f"rv:{line.id}:align")
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=777),
        effective_chat=SimpleNamespace(id=777, type="private"),
        effective_message=SimpleNamespace(chat=SimpleNamespace(id=777, type="private")),
    )
    await bot_reviews.on_callback(update, SimpleNamespace(user_data={}))

    assert query.edits == []
    assert any("owner" in alert for alert in query.alerts)


async def test_list_anomalies_shows_the_lines(session, bot_session_factory) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=OWNER_ID),
        effective_message=message,
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        callback_query=None,
    )

    await bot_reviews.list_anomalies(update, SimpleNamespace(user_data={}))

    body, markup = message.sent[-1]
    assert "Anomalie da sistemare" in body
    assert "Mario Rossi" in body
    assert _callbacks(markup) == [f"rv:{line.id}"]


async def test_find_by_name_opens_the_line(session, bot_session_factory) -> None:
    line = await _seed_line(session, local=date(2026, 12, 20))
    await store_reviews(
        session, _report(_local_ahead(line.id, date(2026, 12, 20), date(2026, 11, 20)))
    )
    await session.commit()
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=OWNER_ID),
        effective_message=message,
        effective_chat=SimpleNamespace(id=OWNER_ID, type="private"),
        callback_query=None,
    )

    await bot_reviews.find(update, SimpleNamespace(args=["mario"], user_data={}))

    body, markup = message.sent[-1]
    assert "Mario Rossi" in body
    assert _callbacks(markup) == [f"rv:{line.id}"]
