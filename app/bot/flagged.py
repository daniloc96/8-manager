from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.db import session_scope
from app.services.no_renewal import NoRenewalError, set_no_renewal
from app.services.queries import get_line

logger = logging.getLogger(__name__)

PROMPT = (
    "🚫 <b>Non rinnovare</b>\n\n"
    "{title}\n"
    "Scadenza: <b>{expires}</b>\n\n"
    "Segno la linea come «da non rinnovare».\n"
    "Non cambia niente nel pannello: è un promemoria per te, e la linea "
    "sparisce dal bot solo quando la cancelli lì e il sync la trova sparita."
)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        return
    line_id = int(parts[1])
    mode = parts[2]

    async with session_scope() as session:
        access = await access_for(update, session)
        if not (access.is_owner and access.can_act):
            await query.answer("Solo l'owner può marcare una linea.", show_alert=True)
            return
        await query.answer()

        if mode == "set":
            # Prima mostriamo cosa succede, poi l'owner conferma.
            line = await get_line(session, line_id)
            if line is None:
                await query.edit_message_text("Linea non trovata o disattivata.")
                return
            body = PROMPT.format(
                title=ui.esc(ui.line_title(line)),
                expires=ui.italian_date(line.expires_on),
            )
            markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🚫 Confermo", callback_data=f"nr:{line_id}:confirm"),
                        InlineKeyboardButton("⬅️ No", callback_data="menu:due"),
                    ]
                ]
            )
            await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
            return

        flagged = mode == "confirm"
        user = update.effective_user
        try:
            line, changed = await set_no_renewal(
                session,
                line_id,
                flagged,
                actor_telegram_id=user.id if user else None,
            )
        except NoRenewalError as exc:
            await query.edit_message_text(f"⚠️ {ui.esc(exc)}")
            return
        title = ui.line_title(line)
        expires = line.expires_on

    if changed and flagged:
        body = (
            f"🚫 <b>Segnata come da non rinnovare</b>\n\n{ui.esc(title)}\n"
            f"Scade il {ui.italian_date(expires)}.\n\n"
            "La troverai nel reminder finché non scade. Per annullare: "
            "<b>Rinnova</b> dalla scheda."
        )
    elif changed:
        body = f"↩️ <b>Marcatura tolta</b>\n\n{ui.esc(title)}\nScade il {ui.italian_date(expires)}."
    else:
        body = f"🔄 <b>Già in questo stato</b>\n\n{ui.esc(title)}"

    await query.edit_message_text(
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Apri scheda", callback_data=f"card:{line_id}")]]
        ),
    )
