from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app import __version__
from app.bot import (
    codes,
    common,
    customer,
    editline,
    flagged,
    group_request,
    lines,
    menu,
    newline,
    panels,
    reseller,
    search,
    settings_bot,
)
from app.config import get_settings
from app.db import dispose_engine
from app.jobs.backup import backup_job
from app.jobs.heartbeat import heartbeat_job, write_heartbeat
from app.jobs.reminders import reminder_job
from app.paths import resolve_data_dir

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand("start", "Menu principale"),
    BotCommand("scadenze", "Scadute, oggi e prossimi giorni"),
    BotCommand("cerca", "Cerca una linea"),
    BotCommand("incassi", "Rinnovi del mese"),
    BotCommand("nuovo", "Nuovo cliente"),
    BotCommand("richiedi", "Richiedi un rinnovo (rivenditori)"),
    BotCommand("collega", "Collega questo gruppo a un rivenditore"),
    BotCommand("stato", "Stato del bot e del database"),
    BotCommand("sync", "Sincronizza le linee dai pannelli"),
    BotCommand("id", "Mostra ID utente e chat"),
    BotCommand("help", "Comandi disponibili"),
]


async def richiedi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """In gruppo la richiesta riparte in privato, altrimenti si apre qui."""
    if await reseller.request_in_group(update, context):
        return
    await reseller.ask_which_line(update, context)


async def _post_init(application: Application) -> None:
    resolve_data_dir()
    write_heartbeat()
    await application.bot.set_my_commands(COMMANDS)
    if application.job_queue is not None:
        application.job_queue.run_repeating(
            heartbeat_job,
            interval=60,
            first=60,
            name="heartbeat",
        )
        settings = get_settings()
        tz = ZoneInfo(settings.tz)
        application.job_queue.run_daily(
            reminder_job,
            time=datetime.time(hour=settings.reminder_hour, tzinfo=tz),
            name="reminder",
        )
        application.job_queue.run_daily(
            backup_job,
            time=datetime.time(hour=3, minute=15, tzinfo=tz),
            name="backup",
        )
    me = await application.bot.get_me()
    logger.info("Avviato @%s · versione %s", me.username, __version__)


async def _post_shutdown(application: Application) -> None:
    await dispose_engine()
    logger.info("Bot fermato")


def build_application() -> Application:
    settings = get_settings()
    token, _owner_id = settings.require_runtime()

    application = (
        Application.builder()
        .token(token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    application.add_handler(CommandHandler("start", menu.start))
    application.add_handler(CommandHandler("scadenze", menu.show_due))
    application.add_handler(CommandHandler("cerca", search.cerca))
    application.add_handler(CommandHandler("incassi", menu.show_income))
    application.add_handler(CommandHandler("nuovo", newline.start))
    application.add_handler(CommandHandler("richiedi", richiedi))
    application.add_handler(CommandHandler("collega", reseller.collega))
    application.add_handler(CommandHandler("scollega", reseller.scollega))
    application.add_handler(CommandHandler("id", common.show_id))
    application.add_handler(CommandHandler("stato", common.status))
    application.add_handler(CommandHandler("sync", panels.sync_panels))
    application.add_handler(CommandHandler("help", common.help_command))
    application.add_handler(CallbackQueryHandler(menu.on_callback, pattern=r"^menu:"))
    application.add_handler(
        CallbackQueryHandler(lines.on_callback, pattern=r"^(card|renew|undo|price|history):")
    )
    application.add_handler(CallbackQueryHandler(editline.on_callback, pattern=r"^edit:"))
    application.add_handler(CallbackQueryHandler(customer.on_callback, pattern=r"^cust:"))
    application.add_handler(
        CallbackQueryHandler(customer.on_reseller_callback, pattern=r"^custres:")
    )
    application.add_handler(CallbackQueryHandler(codes.on_callback, pattern=r"^codes:"))
    application.add_handler(CallbackQueryHandler(settings_bot.on_callback, pattern=r"^set:"))
    application.add_handler(CallbackQueryHandler(flagged.on_callback, pattern=r"^nr:"))
    application.add_handler(
        CallbackQueryHandler(reseller.on_callback, pattern=r"^(rq|rqm|rqa|rqr|auth):")
    )
    application.add_handler(CallbackQueryHandler(group_request.ask, pattern=r"^greq:"))
    application.add_handler(CallbackQueryHandler(group_request.confirm, pattern=r"^greqy:"))
    application.add_handler(CallbackQueryHandler(group_request.cancel, pattern=r"^greqn:"))
    application.add_handler(CallbackQueryHandler(newline.on_callback, pattern=r"^new:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search.on_text))
    application.add_error_handler(common.on_error)
    return application


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    application = build_application()
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
