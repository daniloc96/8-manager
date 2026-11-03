"""I flussi che scrivono sia da bottone sia da comando devono funzionare.

`Message.reply_html` di PTB imposta il parse mode da sé e **non** accetta
`parse_mode`: passarglielo solleva `TypeError` a runtime. I finti messaggi di
questi test hanno la stessa firma, così l'errore non passa più inosservato.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date
from types import SimpleNamespace

import pytest

import app.db as db_module
from app.bot import menu
from app.bot import reseller as reseller_bot
from app.models import Customer, Line, Reseller, Service
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

    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


class StrictMessage:
    """Imita `telegram.Message.reply_html`, che parse_mode non lo accetta."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.markup = None

    async def reply_html(self, text, **kwargs):
        if "parse_mode" in kwargs:
            raise TypeError("Message.reply_html() got an unexpected keyword argument 'parse_mode'")
        self.sent.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


def group_update(message, *, user_id: int = OWNER_ID) -> SimpleNamespace:
    chat = SimpleNamespace(id=-100123, type="supergroup", title="Gruppo Mario")
    return SimpleNamespace(
        callback_query=None,
        effective_chat=chat,
        effective_message=message,
        effective_user=SimpleNamespace(id=user_id, first_name="Danilo"),
    )


async def _reseller(session) -> Reseller:
    reseller = Reseller(name="Mario", name_key="mario", telegram_user_id=777, active=True)
    session.add(reseller)
    await session.commit()
    return reseller


async def test_collega_group_does_not_pass_parse_mode(session, bot_session_factory) -> None:
    await _reseller(session)
    message = StrictMessage()
    update = group_update(message)
    context = SimpleNamespace(args=["Mario"], user_data={})

    await reseller_bot.collega(update, context)

    assert message.sent
    assert "Gruppo collegato a" in message.sent[0]


async def test_scollega_group_does_not_pass_parse_mode(session, bot_session_factory) -> None:
    await _reseller(session)
    message = StrictMessage()
    update = group_update(message)

    await reseller_bot.scollega(update, SimpleNamespace(args=[], user_data={}))

    assert message.sent


async def test_settings_as_command_uses_a_safe_target(session, bot_session_factory) -> None:
    """Da bottone passa `edit_message_text`, da comando `reply_html`: entrambi ok."""
    message = StrictMessage()
    update = group_update(message)

    await menu.show_settings(update, SimpleNamespace(user_data={}))

    assert message.sent
    assert "Impostazioni" in message.sent[0]


async def test_reseller_request_command_uses_a_safe_target(session, bot_session_factory) -> None:
    reseller = await _reseller(session)
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
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
            reseller_id=reseller.id,
        )
    )
    await session.commit()

    message = StrictMessage()
    update = group_update(message, user_id=777)

    await reseller_bot.ask_which_line(update, SimpleNamespace(user_data={}))

    assert message.sent


def test_no_reply_html_call_passes_parse_mode() -> None:
    """Guardia statica: `reply_html` non accetta parse_mode, mai.

    Un simile errore in produzione costa un comando rotto e un'eccezione
    all'owner; il controllo gira su tutto `app/`, non solo sui flussi testati.
    """
    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "reply_html"
                and any(keyword.arg == "parse_mode" for keyword in node.keywords)
            ):
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == [], f"reply_html con parse_mode: {offenders}"


def test_reply_html_is_never_used_as_a_value() -> None:
    """`target = message.reply_html` porta lo stesso errore a distanza.

    Chi ha bisogno di un callable che accetti parse_mode usa `ui.html_target`.
    """
    offenders: list[str] = []
    for path in pathlib.Path("app").rglob("*.py"):
        tree = ast.parse(path.read_text())
        # Le chiamate sono Attribute dentro Call: qui contano solo gli usi come valore.
        called = {
            id(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "reply_html"
                and id(node) not in called
            ):
                offenders.append(f"{path}:{node.lineno}")

    assert offenders == [], f"reply_html usato come callable: {offenders}"
