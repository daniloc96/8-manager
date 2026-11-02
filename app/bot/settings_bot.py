from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.db import session_scope
from app.services import settings as settings_service
from app.services.settings import SettingValueError

logger = logging.getLogger(__name__)


def _cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Annulla", callback_data="set:cancel")]


async def _is_owner(update: Update) -> bool:
    async with session_scope() as session:
        access = await access_for(update, session)
    return access.is_owner and access.can_act


async def ask_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    key = query.data.partition(":")[2]

    definition = settings_service.editable(key)
    if definition is None:
        await query.answer("Impostazione sconosciuta.", show_alert=True)
        return
    if not await _is_owner(update):
        await query.answer("Solo l'owner può cambiare le impostazioni.", show_alert=True)
        return
    await query.answer()

    async with session_scope() as session:
        current = await settings_service.read_value(session, key)

    context.user_data["awaiting"] = f"setting:{key}"
    shown = f"<code>{ui.esc(current)}</code>" if current else "<i>non impostato</i>"
    await query.edit_message_text(
        f"⚙️ <b>{ui.esc(definition.label)}</b>\n\n"
        f"Valore attuale: {shown}\n"
        f"<i>{ui.esc(definition.hint)}</i>\n\n"
        "Scrivi il nuovo valore, oppure <code>-</code> per tornare al default.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([_cancel_row()]),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return

    if query.data == "set:cancel":
        context.user_data.pop("awaiting", None)
        await query.answer()
        from app.bot.menu import show_settings

        await show_settings(update, context)
        return

    await ask_value(update, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Testo in attesa di un valore di impostazione. True se consumato."""
    pending = context.user_data.get("awaiting")
    message = update.effective_message
    if not isinstance(pending, str) or not pending.startswith("setting:"):
        return False
    if message is None or message.text is None:
        return False

    key = pending.partition(":")[2]
    if settings_service.editable(key) is None:
        context.user_data.pop("awaiting", None)
        await message.reply_html("Impostazione sconosciuta.")
        return True

    async with session_scope() as session:
        access = await access_for(update, session)
        if not (access.is_owner and access.can_act):
            context.user_data.pop("awaiting", None)
            await message.reply_html("Solo l'owner può cambiare le impostazioni.")
            return True

        try:
            previous, changed = await settings_service.set_value(session, key, message.text)
            current = await settings_service.read_value(session, key)
        except SettingValueError as exc:
            # Lo stato resta in attesa: si corregge senza ripartire.
            await message.reply_html(
                f"⚠️ {ui.esc(exc)}\n\nRiprova, oppure premi Annulla.",
                reply_markup=InlineKeyboardMarkup([_cancel_row()]),
            )
            return True

    context.user_data.pop("awaiting", None)
    definition = settings_service.edit_definition(key)
    label = definition.label if definition else key
    shown = f"<code>{ui.esc(current)}</code>" if current else "<i>default</i>"

    if changed:
        before = f" (<code>{ui.esc(previous)}</code>)" if previous else ""
        body = f"✅ <b>{ui.esc(label)}</b> aggiornato{before}\n\nOra è {shown}."
    else:
        body = f"🔄 <b>{ui.esc(label)}</b> invariato: {shown}."

    await message.reply_html(
        body,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⚙️ Impostazioni", callback_data="menu:settings")]]
        ),
    )
    return True
