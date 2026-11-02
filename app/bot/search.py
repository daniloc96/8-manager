from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes

from app.bot import texts, ui
from app.bot.auth import access_for
from app.config import get_settings
from app.db import session_scope
from app.services.queries import search_lines

logger = logging.getLogger(__name__)


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def run_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    user = update.effective_user
    message = update.effective_message
    if message is None:
        return
    if user is None:
        return

    today = _today()
    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.allowed:
            await message.reply_html(texts.UNAUTHORIZED.format(user_id=user.id))
            return
        lines = await search_lines(
            session,
            query,
            today=today,
            reseller_id=None if access.is_owner else access.reseller_id,
        )
        body = [
            texts.SEARCH_TITLE.format(query=ui.esc(query), count=len(lines)),
        ]
        if not lines:
            await message.reply_html(texts.SEARCH_EMPTY.format(query=ui.esc(query)))
            return
        body.append("")
        body.extend(ui.line_row(line, today) for line in lines)
        keyboard = ui.results_keyboard(lines, today)

    context.user_data.pop("awaiting", None)
    await message.reply_html("\n".join(body), reply_markup=keyboard)


async def cerca(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args or []).strip()
    if not query:
        message = update.effective_message
        if message is not None:
            await message.reply_html(texts.SEARCH_PROMPT)
        return
    await run_search(update, context, query)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Testo libero: prima gli input attesi (prezzo, mesi), poi la ricerca."""
    from app.bot.lines import handle_pending_text

    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if message is None or message.text is None or user is None or chat is None:
        return
    if chat.type != "private":
        return
    from app.bot.editline import handle_text as handle_edit_text
    from app.bot.newline import handle_text as handle_new_text
    from app.bot.reseller import handle_request_text
    from app.bot.settings_bot import handle_text as handle_setting_text

    if await handle_new_text(update, context):
        return

    if await handle_edit_text(update, context):
        return

    if await handle_setting_text(update, context):
        return

    pending = context.user_data.get("awaiting")
    if (
        isinstance(pending, str)
        and pending.startswith("reqmonths:")
        and await handle_request_text(update, context, pending)
    ):
        return

    async with session_scope() as session:
        access = await access_for(update, session)
    if not access.allowed:
        await message.reply_html(texts.UNAUTHORIZED.format(user_id=user.id))
        return

    if await handle_pending_text(update, context):
        return
    await run_search(update, context, message.text)
