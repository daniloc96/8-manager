from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.config import get_settings
from app.db import session_scope
from app.services.queries import customer_overview

logger = logging.getLogger(__name__)


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheda cliente: una riga per linea e il totale mensile."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    _, _, raw_id = query.data.partition(":")
    if not raw_id.isdigit():
        return
    customer_id = int(raw_id)

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.allowed:
            await query.edit_message_text("Non sei autorizzato.")
            return

        overview = await customer_overview(
            session,
            customer_id,
            reseller_id=None if access.is_owner else access.reseller_id,
        )
        if overview is None:
            await query.edit_message_text("Cliente non trovato.")
            return
        if not overview.lines:
            await query.edit_message_text("Nessuna linea di questo cliente a te disponibile.")
            return
        body = ui.customer_card(overview, _today(), show_hidden=access.is_owner)
        markup = ui.customer_keyboard(overview)

    await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
