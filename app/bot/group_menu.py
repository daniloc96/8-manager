"""Menu a bottoni nel gruppo del rivenditore.

In gruppo i comandi indirizzati (`/scadenze@bot`, `/richiedi@bot`) funzionano ma
sono scomodi da ricordare: con `/start` compare una pulsantiera con le due cose
che servono davvero, senza prezzi e senza comandi dell'owner.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import is_group
from app.config import get_settings
from app.db import session_scope
from app.jobs.reminders import _today, group_payload, group_rows
from app.models import Reseller
from app.services.queries import lines_of_reseller

logger = logging.getLogger(__name__)

MAX_LINES = 12


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Scadenze", callback_data="gmenu:due"),
                InlineKeyboardButton("🔁 Rinnovo", callback_data="gmenu:renew"),
            ],
            [InlineKeyboardButton("❓ Aiuto", callback_data="gmenu:help")],
        ]
    )


def _back(label: str = "🏠 Menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="gmenu:home")]])


def _menu_text(name: str) -> str:
    return (
        f"👋 <b>{ui.esc(name)}</b>, cosa ti serve?\n\n"
        "📅 <b>Scadenze</b> — le linee in scadenza nei prossimi "
        f"{get_settings().reminder_days} giorni\n"
        "🔁 <b>Rinnovo</b> — scegli la linea e il periodo da richiedere\n\n"
        "Qui vedi solo le tue linee: i prezzi restano in privato."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """`/start` in gruppo: pulsantiera al posto dei comandi. True se gestito."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None or not is_group(chat.type):
        return False

    async with session_scope() as session:
        found = await _reseller_of_chat(update, session)
    if found is None:
        await message.reply_html(
            "Questo gruppo non è ancora collegato a un rivenditore.\n"
            "L'owner lo collega con <code>/collega Nome</code>."
        )
        return True
    _reseller_id, name = found

    await message.reply_html(_menu_text(name), reply_markup=_menu_keyboard())
    return True


async def _reseller_of_chat(update: Update, session) -> tuple[int, str] | None:
    """Il rivenditore collegato a questa chat, non chi sta premendo.

    Nel gruppo del rivenditore anche l'owner vede lo stesso menu: i permessi
    sulle azioni restano quelli di `group_request`.
    """
    chat = update.effective_chat
    if chat is None or not is_group(chat.type):
        return None
    reseller = await session.scalar(
        select(Reseller).where(
            Reseller.group_chat_id == chat.id,
            Reseller.active.is_(True),
        )
    )
    if reseller is None:
        return None
    return reseller.id, reseller.name


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    editing = ui.Editor(query)

    async with session_scope() as session:
        found = await _reseller_of_chat(update, session)
        if found is None:
            await editing.edit_text(
                "Questo gruppo non è collegato a un rivenditore: chiedi all'owner."
            )
            return
        reseller_id, name = found

        if query.data == "gmenu:home":
            await editing.edit_text(
                _menu_text(name),
                parse_mode=ParseMode.HTML,
                reply_markup=_menu_keyboard(),
            )
            return

        if query.data == "gmenu:due":
            rows = await group_rows(_today(), reseller_id=reseller_id)
            payload = group_payload(rows)
            if payload is None:
                await editing.edit_text(
                    f"✅ Nessuna scadenza nei prossimi {get_settings().reminder_days} giorni.",
                    reply_markup=_back(),
                )
                return
            body, markup = payload
            with_menu = InlineKeyboardMarkup(
                [
                    *markup.inline_keyboard,
                    [InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")],
                ]
            )
            await editing.edit_text(body, parse_mode=ParseMode.HTML, reply_markup=with_menu)
            return

        if query.data == "gmenu:renew":
            lines = (await lines_of_reseller(session, reseller_id))[:MAX_LINES]
            if not lines:
                await editing.edit_text(
                    "Non ci sono linee attive da rinnovare.", reply_markup=_back()
                )
                return
            buttons = [
                [
                    InlineKeyboardButton(
                        f"{ui.line_title(line)} · {line.expires_on:%d/%m}"[:60],
                        callback_data=f"greq:{line.id}",
                    )
                ]
                for line in lines
            ]
            buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")])
            await editing.edit_text(
                "🔁 <b>Quale linea vuoi rinnovare?</b>\n\n"
                "Scegli la linea, poi il periodo: la richiesta parte solo dopo la conferma.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        await editing.edit_text(
            "❓ <b>Come funziona</b>\n\n"
            "📅 <b>Scadenze</b> mostra le linee tue in scadenza, con il pulsante "
            "«Richiedi» su ognuna.\n"
            "🔁 <b>Rinnovo</b> fa scegliere linea e periodo; prima di inviare ti "
            "chiedo conferma.\n\n"
            "Fuori dal menu restano i comandi <code>/scadenze</code> e "
            "<code>/richiedi</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back(),
        )
