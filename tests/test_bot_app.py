from __future__ import annotations

from app.config import get_settings


def test_build_application_registers_handlers(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "42")
    get_settings.cache_clear()
    try:
        from app.main import build_application

        application = build_application()
        handlers = application.handlers[0]
        commands = {
            command for handler in handlers for command in getattr(handler, "commands", set())
        }
        assert {
            "start",
            "scadenze",
            "cerca",
            "incassi",
            "richiedi",
            "stato",
            "sync",
            "id",
            "help",
            "nuovo",
        } <= commands
        assert len(handlers) >= 10
    finally:
        get_settings.cache_clear()


def test_menu_and_line_callbacks_are_disjoint() -> None:
    """I pattern dei CallbackQueryHandler non devono sovrapporsi."""
    import re

    menu = re.compile(r"^menu:")
    line = re.compile(r"^(card|renew|undo|price|history|off):")
    request = re.compile(r"^(rq|rqm|rqa|rqr|auth):")
    wizard = re.compile(r"^new:")
    edit = re.compile(r"^edit:")
    cliente = re.compile(r"^cust:")

    samples = [
        "menu:home",
        "menu:due",
        "card:12",
        "renew:12:pick",
        "renew:12:3",
        "undo:44",
        "price:12",
        "history:12",
        "off:12:ask",
        "off:12:yes",
        "rq:12",
        "rqm:12:3",
        "rqa:5:3",
        "rqr:5",
        "auth:777:yes",
        "new:create",
        "new:service:live_vod",
        "edit:12",
        "edit:12:alias",
        "edit:12:code:live_hallo",
        "cust:5",
    ]
    for data in samples:
        matches = [
            bool(menu.match(data)),
            bool(line.match(data)),
            bool(request.match(data)),
            bool(wizard.match(data)),
            bool(edit.match(data)),
            bool(cliente.match(data)),
        ]
        assert sum(matches) == 1, data
