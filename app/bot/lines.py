from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.config import get_settings
from app.db import session_scope
from app.models import AuditLog, Platform, RenewalSource
from app.services.lines import renew_line
from app.services.months import MAX_MONTHS, MIN_MONTHS
from app.services.queries import get_line, panel_for
from app.services.reviews import describe, open_issue
from app.services.text import format_eur, parse_price_to_cents

logger = logging.getLogger(__name__)

UNDO_WINDOW = timedelta(minutes=15)


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def _back(extra: list[list[InlineKeyboardButton]] | None = None) -> InlineKeyboardMarkup:
    rows = list(extra or [])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


async def send_card(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    line_id: int,
    *,
    note: str | None = None,
) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()

    # Aprire una scheda è una navigazione: l'input in attesa (mesi o prezzo)
    # decade qui, altrimenti il numero digitato dopo un «Annulla» finirebbe
    # nel flusso abbandonato.
    context.user_data.pop("awaiting", None)

    async with session_scope() as session:
        access = await access_for(update, session)
        line = await get_line(session, line_id)
        if line is None:
            body = "Linea non trovata o disattivata."
            markup = _back()
            issue = None
        elif access.is_reseller and line.reseller_id != access.reseller_id:
            body = "Linea non disponibile."
            markup = _back()
            issue = None
        elif not access.allowed:
            body = "Non sei autorizzato."
            markup = _back()
            issue = None
        else:
            issue = await open_issue(session, line_id=line.id) if access.is_owner else None
            body = ui.line_card(line, _today())
            if issue is not None:
                body = f"{body}\n⚠️ <i>{ui.esc(describe(issue))}</i>"
            if note:
                body = f"{note}\n\n{body}"
            markup = ui.line_keyboard(
                line,
                is_owner=access.is_owner,
                is_reseller=access.is_reseller,
                has_issue=issue is not None,
            )

    if query is not None:
        await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        message = update.effective_message
        if message is not None:
            await message.reply_html(body, reply_markup=markup)


async def card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, raw_id = query.data.split(":", 1)
    await send_card(update, context, int(raw_id))


async def pick_months(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not await _owner_only(update, query):
        return
    await query.answer()
    _, raw_id, _action = query.data.split(":")
    line_id = int(raw_id)

    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        presets = get_settings().duration_presets_list
        body = f"{ui.line_card(line, _today())}\n\n<b>Quanti mesi?</b>"

    await query.edit_message_text(
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=ui.months_keyboard(line, presets),
    )


async def _owner_only(update: Update, query) -> bool:
    async with session_scope() as session:
        access = await access_for(update, session)
    if access.is_owner:
        return True
    await query.answer("Solo l'owner può modificare le linee.", show_alert=True)
    return False


async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not await _owner_only(update, query):
        return
    _, raw_id, raw_months = query.data.split(":")
    line_id = int(raw_id)

    if raw_months == "other":
        await query.answer()
        context.user_data["awaiting"] = f"months:{line_id}"
        await query.edit_message_text(
            "Quanti mesi? Scrivi un numero da 1 a 36.",
            reply_markup=_back(
                [[InlineKeyboardButton("⬅️ Annulla", callback_data=f"card:{line_id}")]]
            ),
        )
        return

    await query.answer()
    await _apply_renewal(update, context, line_id, int(raw_months), editing=ui.Editor(query))


async def _apply_renewal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    line_id: int,
    months: int,
    *,
    editing,
) -> None:
    if not MIN_MONTHS <= months <= MAX_MONTHS:
        await editing.edit_text(f"I mesi devono stare tra {MIN_MONTHS} e {MAX_MONTHS}.")
        return

    today = _today()
    user = update.effective_user
    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await editing.edit_text("Linea non trovata o disattivata.")
            return

        previous = {
            "expires_on": line.expires_on.isoformat(),
            "anchor_day": line.anchor_day,
            "monthly_price_cents": line.monthly_price_cents,
        }
        renewal = await renew_line(
            session,
            line,
            months=months,
            today=today,
            source=RenewalSource.CARD,
            actor_telegram_id=user.id if user else None,
        )
        session.add(
            AuditLog(
                action="renewal_card",
                entity="line",
                entity_id=line.id,
                payload={
                    "months": months,
                    "total_cents": renewal.total_cents,
                    **previous,
                    "new_expires_on": line.expires_on.isoformat(),
                },
            )
        )
        await session.flush()

        context.user_data["undo"] = {
            "renewal_id": renewal.id,
            "line_id": line.id,
            "at": datetime.now(UTC).isoformat(),
            **previous,
        }

        body = (
            f"✅ <b>Rinnovo registrato</b>\n\n"
            f"{ui.esc(ui.line_title(line))}\n"
            f"{months} {'mese' if months == 1 else 'mesi'} · "
            f"{format_eur(renewal.total_cents)}\n"
            f"Nuova scadenza: <b>{ui.italian_date(line.expires_on)}</b>"
        )
        markup = ui.undo_keyboard(renewal.id, line.id)

    context.user_data.pop("awaiting", None)
    await editing.edit_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not await _owner_only(update, query):
        return
    _, raw_renewal = query.data.split(":", 1)
    renewal_id = int(raw_renewal)

    stored = context.user_data.get("undo")
    if not stored or stored.get("renewal_id") != renewal_id:
        await query.answer("Niente da annullare: operazione troppo vecchia.", show_alert=True)
        return

    applied_at = datetime.fromisoformat(stored["at"])
    if datetime.now(UTC) - applied_at > UNDO_WINDOW:
        context.user_data.pop("undo", None)
        await query.answer("Sono passati più di 15 minuti: rinnovo confermato.", show_alert=True)
        return

    from app.models import Line, Renewal

    async with session_scope() as session:
        renewal = await session.get(Renewal, renewal_id)
        line = await session.get(Line, stored["line_id"])
        if renewal is None or line is None:
            await query.answer("Rinnovo non trovato.", show_alert=True)
            return
        await session.delete(renewal)
        line.expires_on = date_from_iso(stored["expires_on"])
        line.anchor_day = int(stored["anchor_day"])
        line.monthly_price_cents = int(stored["monthly_price_cents"])
        session.add(
            AuditLog(
                action="renewal_undo",
                entity="line",
                entity_id=line.id,
                payload={"renewal_id": renewal_id},
            )
        )
        await session.flush()
        title = ui.line_title(line)
        expires = line.expires_on

    context.user_data.pop("undo", None)
    await query.answer("Rinnovo annullato")
    await query.edit_message_text(
        f"↩️ <b>Rinnovo annullato</b>\n\n{ui.esc(title)}\n"
        f"Scadenza riportata a {ui.italian_date(expires)}",
        parse_mode=ParseMode.HTML,
        reply_markup=_back(),
    )


def date_from_iso(value: str):
    return datetime.fromisoformat(value).date()


async def ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not await _owner_only(update, query):
        return
    await query.answer()
    _, raw_id = query.data.split(":", 1)
    line_id = int(raw_id)
    context.user_data["awaiting"] = f"price:{line_id}"
    await query.edit_message_text(
        "💶 Scrivi il nuovo prezzo mensile in euro (es. <code>5</code> o <code>5,50</code>).",
        parse_mode=ParseMode.HTML,
        reply_markup=_back([[InlineKeyboardButton("⬅️ Annulla", callback_data=f"card:{line_id}")]]),
    )


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    _, raw_id = query.data.split(":", 1)
    line_id = int(raw_id)

    async with session_scope() as session:
        access = await access_for(update, session)
        line = await get_line(session, line_id)
        if line is not None and access.is_reseller and line.reseller_id != access.reseller_id:
            await query.edit_message_text("Linea non disponibile.")
            return
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        renewals = list(line.renewals)
        rows = [
            f"• {ui.italian_date(item.period_end)} · {item.months} m · "
            f"{format_eur(item.total_cents)} · {item.source.value}"
            for item in renewals[-10:]
        ]
        live = panel_for(line, Platform.LIVE_HALLO)
        vod = panel_for(line, Platform.VOD_NEWVOD)
        panel_info = []
        if live and live.expires_on:
            panel_info.append(f"Live {ui.italian_date(live.expires_on)}")
        if vod and vod.expires_on:
            panel_info.append(f"VOD {ui.italian_date(vod.expires_on)}")
        body = [f"🕘 <b>Storico</b> · {ui.esc(ui.line_title(line))}", ""]
        body.extend(rows or ["Nessun rinnovo registrato."])
        if panel_info:
            body.extend(["", "Pannello: " + " · ".join(panel_info)])

    await query.edit_message_text(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=_back([[InlineKeyboardButton("⬅️ Scheda", callback_data=f"card:{line_id}")]]),
    )


async def handle_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Gestisce prezzo e mesi in attesa. True se il testo è stato consumato."""
    pending = context.user_data.get("awaiting")
    if not pending or not isinstance(pending, str):
        return False

    message = update.effective_message
    if message is None or message.text is None:
        return False

    kind, _, raw_id = pending.partition(":")
    if kind == "price" and raw_id.isdigit():
        cents = parse_price_to_cents(message.text)
        if cents is None:
            await message.reply_html(
                "Prezzo non valido. Scrivi un numero, per esempio <code>5</code>."
            )
            return True

        async with session_scope() as session:
            line = await get_line(session, int(raw_id))
            if line is None:
                context.user_data.pop("awaiting", None)
                await message.reply_html("Linea non trovata o disattivata.")
                return True
            previous = line.monthly_price_cents
            line.monthly_price_cents = cents
            session.add(
                AuditLog(
                    action="price_set",
                    entity="line",
                    entity_id=line.id,
                    payload={"old": previous, "new": cents},
                )
            )
            title = ui.line_title(line)

        context.user_data.pop("awaiting", None)
        await message.reply_html(
            f"💶 {ui.esc(title)}: prezzo mensile "
            f"{format_eur(previous)} → <b>{format_eur(cents)}</b>"
        )
        return True

    if kind == "months" and raw_id.isdigit():
        text = message.text.strip()
        if not text.isdigit():
            await message.reply_html("Scrivi un numero di mesi da 1 a 36.")
            return True
        await _apply_renewal(
            update,
            context,
            int(raw_id),
            int(text),
            editing=ui.Editor(message),
        )
        return True

    return False


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instradamento dei bottoni delle schede linea."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    data = query.data
    kind = data.split(":", 1)[0]

    if kind == "card":
        await card(update, context)
    elif kind == "renew":
        await pick_months(update, context) if data.endswith(":pick") else await renew(
            update, context
        )
    elif kind == "undo":
        await undo(update, context)
    elif kind == "price":
        await ask_price(update, context)
    elif kind == "history":
        await history(update, context)
