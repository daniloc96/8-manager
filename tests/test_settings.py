from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import settings_bot
from app.models import AuditLog, Setting
from app.services.settings import SettingValueError, read_value, set_value

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
        self.text: str | None = None
        self.markup = None
        self.alerts: list[str] = []

    async def answer(self, text=None, **kwargs) -> None:
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, **kwargs) -> None:
        self.text = text
        self.markup = kwargs.get("reply_markup")


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.sent: list[str] = []

    async def reply_html(self, text, **kwargs):
        self.sent.append(text)
        return self


def _callback(data: str, user_id: int = OWNER_ID):
    query = FakeQuery(data)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=None,
    )
    return update, query


def _text(text: str, user_id: int = OWNER_ID):
    message = FakeMessage(text)
    update = SimpleNamespace(
        callback_query=None,
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=1, type="private"),
        effective_message=message,
    )
    return update, message


async def test_set_value_validates_and_saves(session) -> None:
    previous, changed = await set_value(session, "vod_domain", "http://vod.example.org/")

    assert changed is True
    assert previous is None
    assert await read_value(session, "vod_domain") == "http://vod.example.org"

    entry = await session.scalar(select(AuditLog).where(AuditLog.action == "setting_set"))
    assert entry is not None and entry.payload["new"] == "http://vod.example.org"


async def test_set_value_rejects_bad_domains(session) -> None:
    for bad in ["ftp://x.org", "example.org", "http://x.org/path", "http://x.org?a=1", ""]:
        with pytest.raises(SettingValueError):
            await set_value(session, "vod_domain", bad)


async def test_set_value_rejects_unknown_key(session) -> None:
    with pytest.raises(SettingValueError):
        await set_value(session, "totally_made_up", "x")


async def test_set_value_clears_with_dash(session) -> None:
    await set_value(session, "vod_domain", "http://vod.example.org")

    previous, changed = await set_value(session, "vod_domain", "-")

    assert changed is True
    assert previous == "http://vod.example.org"
    assert await read_value(session, "vod_domain") is None
    assert await session.scalar(select(Setting).where(Setting.key == "vod_domain")) is None


async def test_ask_value_stores_state(session, bot_session_factory) -> None:
    update, query = _callback("set:vod_domain")
    context = SimpleNamespace(user_data={}, bot=None)

    await settings_bot.on_callback(update, context)

    assert context.user_data["awaiting"] == "setting:vod_domain"
    assert "Dominio VOD" in query.text


async def test_text_saves_and_clears_state(session, bot_session_factory) -> None:
    update, message = _text("https://vod.new.org")
    context = SimpleNamespace(user_data={"awaiting": "setting:vod_domain"}, bot=None)

    consumed = await settings_bot.handle_text(update, context)

    assert consumed is True
    assert "awaiting" not in context.user_data
    assert message.sent and "aggiornato" in message.sent[0]
    async with bot_session_factory() as check:
        assert await read_value(check, "vod_domain") == "https://vod.new.org"


async def test_invalid_value_keeps_state_for_retry(session, bot_session_factory) -> None:
    update, message = _text("non-un-dominio")
    context = SimpleNamespace(user_data={"awaiting": "setting:vod_domain"}, bot=None)

    await settings_bot.handle_text(update, context)

    assert context.user_data["awaiting"] == "setting:vod_domain"
    assert message.sent and "⚠️" in message.sent[0]


async def test_non_owner_cannot_open_settings(session, bot_session_factory) -> None:
    update, query = _callback("set:vod_domain", user_id=999)

    await settings_bot.on_callback(update, SimpleNamespace(user_data={}, bot=None))

    assert query.alerts and "owner" in query.alerts[0]
    assert query.text is None
