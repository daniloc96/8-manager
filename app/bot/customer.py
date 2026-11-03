from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.config import get_settings
from app.db import session_scope
from app.services.anagrafica import AnagraficaError, set_customer_reseller
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
        markup = ui.customer_keyboard(overview, is_owner=access.is_owner)

    await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _picker(update: Update, context: ContextTypes.DEFAULT_TYPE, customer_id: int) -> None:
    """Elenco dei rivenditori attivi: la scelta vale per tutte le linee."""
    from sqlalchemy import select

    from app.models import Reseller

    query = update.callback_query
    async with session_scope() as session:
        access = await access_for(update, session)
        if not (access.is_owner and access.can_act):
            await query.answer("Solo l'owner può assegnare i rivenditori.", show_alert=True)
            return
        overview = await customer_overview(session, customer_id)
        if overview is None:
            await query.edit_message_text("Cliente non trovato.")
            return
        resellers = list(
            (await session.scalars(select(Reseller).where(Reseller.active.is_(True)))).all()
        )
        current = "Diretto" if overview.lines and overview.lines[0].reseller is None else None
        if current is None and overview.lines:
            current = overview.lines[0].reseller.name
        name = overview.customer.name
        count = len(overview.lines)

    rows = [[InlineKeyboardButton("Diretto (io)", callback_data=f"custres:{customer_id}:0")]]
    for reseller in resellers:
        mark = "✓ " if reseller.name == current else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark}{reseller.name}", callback_data=f"custres:{customer_id}:{reseller.id}"
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Cliente", callback_data=f"cust:{customer_id}")])
    await query.answer()
    await query.edit_message_text(
        f"🏷 <b>Rivenditore</b> · {ui.esc(name)}\n\n"
        f"{ui.esc(current or 'linee miste')} · {count} linee\n"
        "La scelta vale per tutte le linee attive del cliente.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _apply(
    update: Update, context: ContextTypes.DEFAULT_TYPE, customer_id: int, reseller_id: int | None
) -> None:
    query = update.callback_query
    async with session_scope() as session:
        access = await access_for(update, session)
        if not (access.is_owner and access.can_act):
            await query.answer("Solo l'owner può assegnare i rivenditori.", show_alert=True)
            return
        try:
            changed, _total = await set_customer_reseller(session, customer_id, reseller_id)
        except AnagraficaError as exc:
            await query.edit_message_text(f"⚠️ {ui.esc(exc)}")
            return
        overview = await customer_overview(session, customer_id)
        if overview is None:
            await query.edit_message_text("Cliente non trovato.")
            return
        who = "Diretto" if reseller_id is None else None
        if who is None and overview.lines:
            who = overview.lines[0].reseller.name if overview.lines[0].reseller else "Diretto"
        body = ui.customer_card(overview, _today(), show_hidden=access.is_owner)
        markup = ui.customer_keyboard(overview, is_owner=access.is_owner)

    head = (
        f"✅ Cliente passato a <b>{ui.esc(who)}</b> · {changed} linee aggiornate"
        if changed
        else f"🔄 Già assegnato a <b>{ui.esc(who)}</b>"
    )
    await query.edit_message_text(
        f"{head}\n\n{body}", parse_mode=ParseMode.HTML, reply_markup=markup
    )


async def on_reseller_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`custres:<cliente>` apre il picker, `custres:<cliente>:<rivenditore>` applica."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    parts = query.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        return
    customer_id = int(parts[1])
    if len(parts) == 2:
        await _picker(update, context, customer_id)
        return
    if not parts[2].isdigit():
        return
    await _apply(update, context, customer_id, int(parts[2]) or None)
