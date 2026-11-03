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


def _callback_patterns(monkeypatch):
    """I pattern veri, presi dall'applicazione costruita.

    Prima questo test ricopiava le regex a mano: una regressione nel routing
    sarebbe passata lo stesso (e c'era un `off:` che in `main.py` non esiste).
    """
    monkeypatch.setenv("BOT_TOKEN", "123456:TEST-TOKEN")
    monkeypatch.setenv("OWNER_TELEGRAM_ID", "42")
    get_settings.cache_clear()
    try:
        from telegram.ext import CallbackQueryHandler

        from app.main import build_application

        application = build_application()
    finally:
        get_settings.cache_clear()

    patterns = []
    for group in application.handlers.values():
        for handler in group:
            if isinstance(handler, CallbackQueryHandler) and handler.pattern is not None:
                patterns.append(handler.pattern)
    return patterns


def test_every_callback_matches_exactly_one_handler(monkeypatch) -> None:
    """I pattern dei CallbackQueryHandler non devono sovrapporsi."""
    patterns = _callback_patterns(monkeypatch)
    assert len(patterns) >= 6

    samples = [
        "menu:home",
        "menu:due",
        "card:12",
        "renew:12:pick",
        "renew:12:3",
        "renew:12:other",
        "undo:44",
        "price:12",
        "history:12",
        "nr:12:set",
        "nr:12:clear",
        "rq:12",
        "rqm:12:3",
        "rqa:5:3",
        "rqr:5",
        "auth:777:yes",
        "new:create",
        "new:service:live_vod",
        "new:skip:alias",
        "edit:12",
        "edit:12:alias",
        "edit:12:code:live_hallo",
        "cust:5",
        "codes:5",
        "custres:5",
        "custres:5:2",
    ]
    for data in samples:
        matches = [pattern for pattern in patterns if pattern.match(data)]
        assert len(matches) == 1, f"{data} matcha {len(matches)} handler"
