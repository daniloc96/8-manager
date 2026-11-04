from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from app.bot import texts
from app.bot.auth import is_owner
from app.db import session_scope
from app.panels.factory import build_adapters
from app.panels.report import report_html
from app.panels.sync import run_sync
from app.services.reviews import store_reviews

logger = logging.getLogger(__name__)


async def sync_panels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    if not is_owner(user.id):
        await message.reply_html(texts.UNAUTHORIZED.format(user_id=user.id))
        return

    chat = update.effective_chat
    if chat is None or chat.type != ChatType.PRIVATE:
        await message.reply_html(texts.SYNC_PRIVATE_ONLY)
        return

    try:
        adapters, skipped = build_adapters()
    except Exception as exc:
        logger.exception("Sync: costruzione adapter fallita")
        await message.reply_html(texts.SYNC_ERROR.format(error=html.escape(str(exc)[:200])))
        return

    if not adapters:
        await message.reply_html(texts.SYNC_NO_PANELS)
        return

    import_missing = bool(context.args) and context.args[0].lower() in {"import", "importa"}
    notice = await message.reply_html(
        texts.SYNC_RUNNING_IMPORT if import_missing else texts.SYNC_RUNNING
    )

    report = await run_sync(adapters, import_missing=import_missing, skipped=skipped)
    async with session_scope() as session:
        await store_reviews(session, report)
    from app.bot.line_request import announce_ready_requests

    await announce_ready_requests(context)
    body = report_html(report)
    try:
        await notice.edit_text(body, parse_mode=ParseMode.HTML)
    except Exception:
        logger.exception("Sync: impossibile modificare il messaggio di stato")
        await message.reply_html(body)
