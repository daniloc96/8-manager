"""Anomalie del sync gestite una alla volta, dall'owner in privato.

Il `/sync` dice *quante* e *quali* righe hanno qualcosa che non torna, ma non
permette di fare niente. Qui l'owner cerca la linea, vede cosa non torna e
sceglie: allineare la data a quella del pannello (fonte di verità) oppure
tenere il locale e segnare lo scarto come accettato.

Callback:
    rv:<linea>            scheda anomalia della linea
    rv:<linea>:align      allinea il locale al pannello
    rv:<linea>:accept     tiene il locale (eccezione fino alla scadenza locale)
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.db import session_scope
from app.services.queries import get_line, search_lines
from app.services.review_exceptions import ReviewExceptionError
from app.services.reviews import accept_local, align_to_panel, describe, open_issue, open_issues

logger = logging.getLogger(__name__)

PREFIX = "rv:"


class NotOwner(Exception):
    """Chi preme non è l'owner."""


def _buttons(line_id: int, customer_id: int, *, solvable: bool) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    if solvable:
        rows.append(
            [
                InlineKeyboardButton(
                    "🗓 Allinea alla data del pannello", callback_data=f"rv:{line_id}:align"
                )
            ]
        )
        rows.append(
            [InlineKeyboardButton("📌 Tieni la data locale", callback_data=f"rv:{line_id}:accept")]
        )
    rows.append([InlineKeyboardButton("⬅️ Cliente", callback_data=f"cust:{customer_id}")])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    return rows


async def _require_owner(update: Update, query) -> bool:
    async with session_scope() as session:
        access = await access_for(update, session)
    if access.is_owner:
        return True
    await query.answer("Solo l'owner gestisce le anomalie.", show_alert=True)
    return False


async def list_anomalies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/anomalie`: elenco delle linee con qualcosa da sistemare."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await message.reply_html("Solo l'owner vede le anomalie del sync.")
            return
        issues = await open_issues(session)
        if not issues:
            await message.reply_html(
                "✅ <b>Nessuna anomalia aperta</b>\n\n"
                "Se il <code>/sync</code> ne ha appena trovate, arrivano qui."
            )
            return
        body = [f"⚠️ <b>Anomalie da sistemare</b> ({len(issues)})", ""]
        for record, line in issues:
            body.append(f"• {ui.esc(ui.line_title(line))} — {ui.esc(describe(record))}")
        body.append("")
        body.append("<i>Premi il cliente per gestirlo.</i>")
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(f"⚠️ {ui.line_title(line)}", callback_data=f"rv:{line.id}")]
                for _record, line in issues[:20]
            ]
        )

    await message.reply_html("\n".join(body), reply_markup=keyboard)


async def attach(update: Update, context: ContextTypes.DEFAULT_TYPE, line_id: int) -> None:
    """Mostra la scheda dell'anomalia di una linea (chiamata dalla scheda cliente)."""
    query = update.callback_query
    if query is None:
        return
    if not await _require_owner(update, query):
        return
    await query.answer()

    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        record = await open_issue(session, line_id=line_id)
        if record is None:
            body = (
                f"✅ <b>Nessuna anomalia</b>\n\n{ui.esc(ui.line_title(line))}\n"
                f"Scadenza locale: <b>{ui.italian_date(line.expires_on)}</b>"
            )
            markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Cliente", callback_data=f"cust:{line.customer_id}")]]
            )
            await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
            return
        title = ui.line_title(line)
        local = line.expires_on
        problem = record.reason
        panel = record.panel_expires
        customer_id = line.customer_id
        solvable = record.issue == "local_ahead" and panel is not None

    body = [
        "⚠️ <b>Anomalia del sync</b>",
        "",
        f"{ui.esc(title)}",
        f"Scadenza locale: <b>{ui.italian_date(local)}</b>",
    ]
    if panel is not None:
        body.append(f"Scadenza nel pannello: <b>{ui.italian_date(panel)}</b>")
    body.append("")
    body.append(ui.esc(problem))
    body.append("")
    if solvable:
        body.append(
            "<i>Il pannello è la fonte di verità: allinea, oppure tieni il locale "
            "se il rinnovo va ancora fatto lì.</i>"
        )
    else:
        body.append("<i>Questa anomalia si sistema sul pannello.</i>")

    await query.edit_message_text(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(_buttons(line_id, customer_id, solvable=solvable)),
    )


async def _apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    parts = query.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        return
    line_id = int(parts[1])
    action = parts[2]
    if not await _require_owner(update, query):
        return

    user = update.effective_user
    actor = user.id if user is not None else None

    async with session_scope() as session:
        try:
            if action == "align":
                line, previous, target = await align_to_panel(
                    session, line_id=line_id, actor_telegram_id=actor
                )
                title = ui.line_title(line)
                await query.answer("Data allineata al pannello")
                body = (
                    "✅ <b>Data allineata</b>\n\n"
                    f"{ui.esc(title)}\n"
                    f"Da {ui.italian_date(previous)} a <b>{ui.italian_date(target)}</b>."
                )
            elif action == "accept":
                line, local = await accept_local(session, line_id=line_id, actor_telegram_id=actor)
                title = ui.line_title(line)
                await query.answer("Data locale mantenuta")
                body = (
                    "📌 <b>Tengo la data locale</b>\n\n"
                    f"{ui.esc(title)}\n"
                    f"Restano {ui.italian_date(local)}: l'anomalia non torna "
                    "finché il pannello non viene rinnovato."
                )
            else:
                return
        except ReviewExceptionError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        customer_id = line.customer_id

    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Cliente", callback_data=f"cust:{customer_id}")]]
    )
    await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    parts = query.data.split(":")
    if len(parts) == 3 and parts[2] in {"align", "accept"}:
        await _apply(update, context)
        return
    if len(parts) == 2 and parts[1].isdigit():
        await attach(update, context, int(parts[1]))


async def find(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/anomalia <nome>`: cerca la linea e apre direttamente la scheda."""
    query_text = " ".join(context.args or []).strip()
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if not query_text:
        await list_anomalies(update, context)
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await message.reply_html("Solo l'owner gestisce le anomalie.")
            return
        lines = await search_lines(session, query_text, today=None)
        found = [(line, await open_issue(session, line_id=line.id)) for line in lines]
        found = [(line, record) for line, record in found if record is not None]
        if not found:
            await message.reply_html(f"Nessuna anomalia aperta per «{ui.esc(query_text)}».")
            return
        if len(found) == 1:
            line = found[0][0]
            line_id = line.id
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("⚠️ Gestisci", callback_data=f"rv:{line_id}")]]
            )
            await message.reply_html(
                f"⚠️ {ui.esc(ui.line_title(line))} — {ui.esc(describe(found[0][1]))}",
                reply_markup=keyboard,
            )
            return
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton(ui.line_title(line), callback_data=f"rv:{line.id}")]
                for line, _record in found[:12]
            ]
        )
        await message.reply_html(
            f"⚠️ <b>Trovate {len(found)} anomalie</b>\n\nScegli la linea.",
            reply_markup=keyboard,
        )
