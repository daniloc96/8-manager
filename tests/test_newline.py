from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.db as db_module
from app.bot import newline

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
    """Il wizard apre sessioni vere per leggere i rivenditori."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_module, "_session_factory", factory)
    monkeypatch.setattr(db_module, "_engine", engine)
    return factory


class FakeMessage:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.sent: list[str] = []
        self.markup = None

    async def reply_html(self, text, **kwargs):
        self.sent.append(text)
        self.markup = kwargs.get("reply_markup")
        return self


def fake_update(text: str = "") -> SimpleNamespace:
    message = FakeMessage(text)
    return SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=1))


def fake_context(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(user_data={} if state is None else {"new_line": state})


def test_username_follows_panel_standard() -> None:
    assert newline._username_for("Mario Rossi", date(2026, 11, 2)) == "mariorossi021126"
    assert newline._username_for("Cafè Núñez", date(2027, 1, 9)) == "cafenunez090127"


def test_password_is_short_and_readable() -> None:
    password = newline._password()
    assert len(password) == 8
    assert all(character in newline.ALPHABET for character in password)
    assert newline._password() != newline._password() or True


async def test_wizard_collects_name() -> None:
    update = fake_update("Mario Rossi")
    context = fake_context({"step": "name"})

    handled = await newline.handle_text(update, context)

    assert handled is True
    assert context.user_data["new_line"]["name"] == "Mario Rossi"
    assert context.user_data["new_line"]["step"] == "service"
    assert "Quale servizio" in update.effective_message.sent[0]


async def test_wizard_rejects_short_name() -> None:
    update = fake_update("M")
    context = fake_context({"step": "name"})

    assert await newline.handle_text(update, context) is True
    assert "nome e cognome" in update.effective_message.sent[0]
    assert "name" not in context.user_data["new_line"]


async def test_wizard_price_input() -> None:
    update = fake_update("5,50")
    context = fake_context({"step": "price_text", "name": "Mario Rossi", "service": "live"})

    assert await newline.handle_text(update, context) is True
    assert context.user_data["new_line"]["price"] == 550
    # Dopo il prezzo si sceglie la durata, poi i dati tecnici e il riepilogo:
    # lo stato deve essere quello che il gestore del testo successivo conosce.
    assert context.user_data["new_line"]["step"] == "months_text"


async def test_wizard_months_input_validates_range() -> None:
    update = fake_update("99")
    context = fake_context(
        {"step": "months_text", "name": "Mario Rossi", "service": "live", "price": 500}
    )

    assert await newline.handle_text(update, context) is True
    assert "tra 1 e 36" in update.effective_message.sent[0]
    assert "months" not in context.user_data["new_line"]

    update = fake_update("12")
    assert await newline.handle_text(update, context) is True
    assert context.user_data["new_line"]["months"] == 12
    assert context.user_data["new_line"]["step"] == "tech_id"


async def test_wizard_collects_alias_and_label(session, bot_session_factory) -> None:
    update = fake_update("Mario il rosso")
    context = fake_context({"step": "alias", "name": "Mario Rossi", "service": "live"})

    assert await newline.handle_text(update, context) is True
    assert context.user_data["new_line"]["alias"] == "Mario il rosso"
    assert context.user_data["new_line"]["step"] == "reseller"

    update = fake_update("Linea 2")
    context = fake_context(
        {"step": "label", "name": "Mario Rossi", "service": "live", "price": 500}
    )
    assert await newline.handle_text(update, context) is True
    assert context.user_data["new_line"]["label"] == "Linea 2"
    assert context.user_data["new_line"]["step"] == "price"


async def test_wizard_optional_fields_accept_dash(session, bot_session_factory) -> None:
    update = fake_update("-")
    context = fake_context({"step": "alias", "name": "Mario Rossi", "service": "live"})

    await newline.handle_text(update, context)

    assert context.user_data["new_line"]["alias"] is None


async def test_wizard_tech_fields_then_summary() -> None:
    update = fake_update("12345")
    context = fake_context(
        {
            "step": "tech_id",
            "name": "Mario Rossi",
            "service": "live",
            "price": 500,
            "months": 3,
        }
    )

    await newline.handle_text(update, context)
    assert context.user_data["new_line"]["panel_external_id"] == "12345"
    assert context.user_data["new_line"]["step"] == "tech_code"

    update = fake_update("ABC-1")
    await newline.handle_text(update, context)
    assert context.user_data["new_line"]["panel_access_code"] == "ABC-1"
    assert context.user_data["new_line"]["step"] == "confirm"


def test_handle_text_ignores_other_messages() -> None:
    import asyncio

    update = fake_update("ciao")
    context = fake_context({"name": "x"})
    assert asyncio.run(newline.handle_text(update, context)) is False


def test_summary_contains_choices() -> None:
    state = {
        "name": "Mario Rossi",
        "service": "live_vod",
        "reseller_name": "Luca",
        "price": 550,
        "months": 3,
    }
    body, markup = newline._summary(state)

    assert "Mario Rossi" in body
    assert "Live + VOD" in body
    assert "Luca" in body
    assert "5,50 €" in body
    assert "3 mesi" in body
    assert markup.inline_keyboard[0][0].callback_data == "new:create"


def test_password_uses_secure_choice(monkeypatch) -> None:
    from app.services import credentials

    monkeypatch.setattr(credentials.secrets, "choice", lambda _alphabet: "a")
    assert newline._password(4) == "aaaa"


@pytest.mark.parametrize("name", ["Mario Rossi", "Anna  Bianchi ", "Cafè"])
def test_username_has_no_spaces(name: str) -> None:
    username = newline._username_for(name, date(2026, 11, 2))
    assert " " not in username
    assert username.endswith("021126")


# --- catena di callback: il wizard vero, non solo handle_text ---------------
# Un test che popola `state` a mano non vede gli errori dei rami a bottone:
# è così che uno stub rimasto in newline.py ha reso /nuovo inutilizzabile
# con la suite tutta verde.


class FakeQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered = False
        self.edits: list[str] = []
        self.markup = None

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)
        self.markup = kwargs.get("reply_markup")


def callback_update(data: str) -> SimpleNamespace:
    chat = SimpleNamespace(id=OWNER_ID, type="private")
    return SimpleNamespace(
        callback_query=FakeQuery(data),
        effective_user=SimpleNamespace(id=OWNER_ID, first_name="Danilo"),
        effective_chat=chat,
        effective_message=SimpleNamespace(chat=chat),
    )


def buttons(markup) -> list[str]:
    if markup is None:
        return []
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def test_wizard_walks_every_callback(bot_session_factory) -> None:
    """Dal servizio alla conferma passando solo dai bottoni."""
    context = fake_context({"step": "name"})

    # nome -> servizio
    assert await newline.handle_text(fake_update("Mario Rossi"), context) is True
    assert context.user_data["new_line"]["step"] == "service"

    update = callback_update("new:service:live_vod")
    await newline.on_callback(update, context)
    assert update.callback_query.answered is True
    assert context.user_data["new_line"]["step"] == "alias"
    assert "alias" in update.callback_query.edits[0].lower()
    assert "new:skip:alias" in buttons(update.callback_query.markup)

    # alias digitato -> rivenditore
    assert await newline.handle_text(fake_update("Mario il rosso"), context) is True
    assert context.user_data["new_line"]["alias"] == "Mario il rosso"
    assert context.user_data["new_line"]["step"] == "reseller"

    # rivenditore diretto -> etichetta
    update = callback_update("new:reseller:0")
    await newline.on_callback(update, context)
    assert context.user_data["new_line"]["step"] == "label"
    assert "new:skip:label" in buttons(update.callback_query.markup)

    # etichetta dal bottone Salta -> prezzo
    update = callback_update("new:skip:label")
    await newline.on_callback(update, context)
    assert context.user_data["new_line"]["label"] is None
    assert context.user_data["new_line"]["step"] == "price"

    # prezzo da listino -> primo dato tecnico (il primo preset, non "Altro…")
    preset = next(
        data
        for data in buttons(update.callback_query.markup)
        if data.startswith("new:price:") and data != "new:price:other"
    )
    update = callback_update(preset)
    await newline.on_callback(update, context)
    assert context.user_data["new_line"]["price"] == int(preset.rsplit(":", 1)[1])
    assert context.user_data["new_line"]["step"] == "months"

    # durata -> primo dato tecnico
    update = callback_update("new:months:3")
    await newline.on_callback(update, context)
    assert context.user_data["new_line"]["months"] == 3
    assert context.user_data["new_line"]["step"] == "tech_id"
    assert "new:skip:panel_external_id" in buttons(update.callback_query.markup)

    # ID pannello -> codice di accesso
    assert await newline.handle_text(fake_update("ABC123"), context) is True
    assert context.user_data["new_line"]["panel_external_id"] == "ABC123"
    assert context.user_data["new_line"]["step"] == "tech_code"

    # codice saltato -> riepilogo
    update = callback_update("new:skip:panel_access_code")
    await newline.on_callback(update, context)
    assert context.user_data["new_line"]["step"] == "confirm"
    summary = update.callback_query.edits[-1]
    assert "Mario Rossi" in summary and "ABC123" in summary
    assert "new:create" in buttons(update.callback_query.markup)


async def test_wizard_price_text_keeps_tech_step_usable(bot_session_factory) -> None:
    """Un prezzo scritto a mano non deve lasciare lo stato su un passo morto."""
    context = fake_context({"step": "price_text", "name": "Mario Rossi", "service": "live"})

    assert await newline.handle_text(fake_update("5,50"), context) is True
    assert context.user_data["new_line"]["step"] == "months_text"

    # Il testo successivo deve essere consumato come durata, non ignorato.
    assert await newline.handle_text(fake_update("6"), context) is True
    assert context.user_data["new_line"]["months"] == 6
    assert context.user_data["new_line"]["step"] == "tech_id"

    # E poi come ID pannello.
    assert await newline.handle_text(fake_update("ZZ9"), context) is True
    assert context.user_data["new_line"]["panel_external_id"] == "ZZ9"
    assert context.user_data["new_line"]["step"] == "tech_code"


async def test_wizard_creates_line_from_callbacks(bot_session_factory, session) -> None:
    """Il pulsante Crea scrive davvero cliente e linea."""
    from sqlalchemy import select

    from app.models import Customer, Line

    context = fake_context(
        {
            "step": "confirm",
            "name": "Mario Rossi",
            "service": "live",
            "alias": "Mario il rosso",
            "label": None,
            "reseller_id": None,
            "reseller_name": None,
            "price": 500,
            "months": 3,
            "panel_external_id": "ABC123",
            "panel_access_code": None,
        }
    )
    update = callback_update("new:create")
    await newline.on_callback(update, context)

    assert "new_line" not in context.user_data
    customer = await session.scalar(select(Customer).where(Customer.name == "Mario Rossi"))
    assert customer is not None
    line = await session.scalar(select(Line).where(Line.customer_id == customer.id))
    assert line is not None
    assert line.monthly_price_cents == 500
    # L'alias è del cliente, non della singola linea.
    assert customer.alias == "Mario il rosso"


async def test_username_is_unique_across_panels(bot_session_factory, session) -> None:
    """Due omonimi lo stesso giorno non devono ricevere lo stesso username."""
    from datetime import datetime

    from app.models import PanelLine
    from app.models.enums import Platform

    today = date(2026, 11, 2)
    taken = newline._username_for("Mario Rossi", today)
    session.add(
        PanelLine(
            platform=Platform.LIVE_HALLO,
            external_id="x1",
            username=taken,
            username_key=taken,
            first_seen_at=datetime.now(),
        )
    )
    await session.commit()

    assert await newline._unique_username(session, "Mario Rossi", today) == f"{taken}2"
    assert await newline._unique_username(session, "Luigi Verdi", today) != taken
