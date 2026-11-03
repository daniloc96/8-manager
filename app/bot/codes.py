"""Vista «Codici»: cosa il pannello espone per le linee di un cliente.

I codici arrivano dal sync, non si inventano: se un pannello non restituisce la
password la si mostra come assente, senza dedurla dal link VOD.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.db import session_scope
from app.models import Line, Platform
from app.services.downloads import load_domains
from app.services.queries import customer_overview
from app.services.vod import download_link

logger = logging.getLogger(__name__)

# Oltre questo numero di linee il messaggio supera il limite di Telegram.
MAX_LINES = 12
MISSING = "—"

LINK_LABELS = {
    Platform.LIVE_HALLO: "link Live",
    Platform.VOD_NEWVOD: "link VOD",
}


def _line_block(line: Line, domains: Mapping[Platform, str]) -> list[str]:
    parts = [f"<b>{ui.esc(ui.line_title(line))}</b>"]
    panels = sorted(line.panel_lines, key=lambda item: item.platform.value)
    if not panels:
        parts.append("<i>Nessuna riga di pannello: arriva con il /sync.</i>")
        return parts

    for panel in panels:
        short = ui.PLATFORM_SHORT.get(panel.platform, panel.platform.value)
        fields = [f"<b>{ui.esc(short)}</b>"]
        fields.append(f"username: <code>{ui.esc(panel.username)}</code>")
        if panel.password:
            fields.append(f"password: <code>{ui.esc(panel.password)}</code>")
        if panel.access_code and panel.access_code != panel.username:
            fields.append(f"codice: <code>{ui.esc(panel.access_code)}</code>")
        elif panel.access_code:
            fields.append("codice: coincide con lo username")
        else:
            fields.append("codice: non presente sul pannello")
        if panel.external_id:
            fields.append(f"ID: <code>{ui.esc(panel.external_id)}</code>")
        if panel.expires_on:
            fields.append(f"scade sul pannello: {ui.italian_date(panel.expires_on)}")
        # Live e VOD hanno ciascuno il proprio dominio e la propria password.
        link_label = LINK_LABELS.get(panel.platform)
        if link_label is not None:
            link = download_link(domains.get(panel.platform), panel.username, panel.password)
            if link is not None:
                fields.append(f"{link_label}: <code>{ui.esc(link)}</code>")
            elif not domains.get(panel.platform):
                fields.append(f"<i>{link_label} non disponibile: dominio non impostato</i>")
            elif not panel.password:
                fields.append(f"<i>{link_label} non disponibile: manca la password</i>")
        if not panel.password and link_label is None:
            if panel.platform == Platform.LIVE_8TVPLUS:
                fields.append("<i>nessuna password: sul pannello si accede con il codice</i>")
            else:
                fields.append("<i>il pannello non espone la password</i>")
        parts.append("\n".join(fields))
    return parts


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`codes:<cliente>`: credenziali e codici di tutte le linee del cliente."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, _, raw_id = query.data.partition(":")
    if not raw_id.isdigit():
        return

    customer_id = int(raw_id)
    async with session_scope() as session:
        access = await access_for(update, session)
        if not (access.is_owner and access.can_act):
            await query.answer("I codici li vede solo l'owner.", show_alert=True)
            return
        overview = await customer_overview(session, customer_id)
        if overview is None:
            await query.answer("Cliente non trovato.", show_alert=True)
            return
        domains = await load_domains(session)
        lines = overview.lines[:MAX_LINES]
        hidden = len(overview.lines) - len(lines)
        blocks = [_line_block(line, domains) for line in lines]
        name = overview.customer.name

    body = [f"🔑 <b>Codici · {ui.esc(name)}</b>", ""]
    for block in blocks:
        body.extend(block)
        body.append("")
    if hidden:
        body.append(f"<i>…e altre {hidden} linee: apri la scheda cliente.</i>")
    body.append("<i>I valori arrivano dal pannello: quello che non espone resta vuoto.</i>")

    await query.answer()
    await query.edit_message_text(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⬅️ Cliente", callback_data=f"cust:{customer_id}")],
                [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
            ]
        ),
    )
