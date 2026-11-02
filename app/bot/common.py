from __future__ import annotations

import html
import logging
import time

from sqlalchemy import func, select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app import __version__
from app.bot import texts
from app.bot.auth import is_owner
from app.config import get_settings
from app.db import session_scope
from app.models import Customer, Line, Renewal
from app.paths import resolve_data_dir

logger = logging.getLogger(__name__)

_last_error_notice = 0.0


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    message = update.effective_message
    if user is None or chat is None or message is None:
        return

    from app.bot.auth import access_for
    from app.db import session_scope

    async with session_scope() as session:
        access = await access_for(update, session)
    role = {"owner": "owner", "reseller": f"rivenditore ({access.name})"}.get(
        access.kind, "sconosciuto"
    )
    await message.reply_html(
        f"<b>Telegram ID</b>: <code>{user.id}</code>\n"
        f"<b>Chat ID</b>: <code>{chat.id}</code>\n"
        f"<b>Tipo chat</b>: {chat.type}\n"
        f"<b>Ruolo</b>: {role}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return
    if is_owner(user.id):
        await message.reply_html(texts.HELP_OWNER)
        return

    from app.bot.auth import access_for
    from app.db import session_scope

    async with session_scope() as session:
        access = await access_for(update, session)
    await message.reply_html(texts.HELP_RESELLER if access.is_reseller else texts.HELP_GUEST)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or not is_owner(user.id):
        return

    customers = lines = renewals = "n/d"
    database = "non raggiungibile"
    try:
        async with session_scope() as session:
            customers = await session.scalar(select(func.count(Customer.id))) or 0
            lines = (
                await session.scalar(select(func.count(Line.id)).where(Line.deleted_at.is_(None)))
                or 0
            )
            renewals = await session.scalar(select(func.count(Renewal.id))) or 0
        database = "ok"
    except Exception as exc:  # il pannello deve restare leggibile anche se il DB è rotto
        logger.exception("Stato: database non raggiungibile")
        database = f"errore: {html.escape(str(exc)[:120])}"

    heartbeat_path = resolve_data_dir() / "heartbeat"
    if heartbeat_path.exists():
        age = max(0, int(time.time() - heartbeat_path.stat().st_mtime))
        heartbeat = f"{age}s fa"
    else:
        heartbeat = "assente"

    await message.reply_html(
        texts.STATUS.format(
            version=__version__,
            database=database,
            customers=customers,
            lines=lines,
            renewals=renewals,
            heartbeat=heartbeat,
        )
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _last_error_notice

    logger.error("Errore non gestito", exc_info=context.error)
    now = time.monotonic()
    if now - _last_error_notice < 60:
        return
    _last_error_notice = now

    settings = get_settings()
    if settings.owner_telegram_id is None:
        return

    error = context.error
    text = "⚠️ <b>Errore nel bot</b>\n<code>{kind}</code>: {message}".format(
        kind=html.escape(type(error).__name__ if error else "Unknown"),
        message=html.escape(str(error or "")[:500]),
    )
    try:
        await context.bot.send_message(
            chat_id=settings.owner_telegram_id,
            text=text,
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.exception("Impossibile notificare l'errore all'owner")
