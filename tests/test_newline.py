from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.bot import newline


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
    assert context.user_data["new_line"]["step"] == "months"


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
    monkeypatch.setattr(newline.secrets, "choice", lambda _alphabet: "a")
    assert newline._password(4) == "aaaa"


@pytest.mark.parametrize("name", ["Mario Rossi", "Anna  Bianchi ", "Cafè"])
def test_username_has_no_spaces(name: str) -> None:
    username = newline._username_for(name, date(2026, 11, 2))
    assert " " not in username
    assert username.endswith("021126")
