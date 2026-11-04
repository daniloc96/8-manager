from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import texts, ui
from app.bot.auth import access_for, is_group
from app.config import get_settings
from app.db import session_scope
from app.services.queries import get_line, lines_of_reseller
from app.services.requests import (
    RequestError,
    approve_request,
    authorize_reseller,
    create_request,
    reject_request,
)
from app.services.text import format_eur

logger = logging.getLogger(__name__)


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def _period(months: int) -> str:
    return "mese" if months == 1 else "mesi"


def _presets() -> tuple[int, ...]:
    return get_settings().duration_presets_list


async def picker_payload(session, reseller_id: int):
    """Testo e bottoni per scegliere la linea da rinnovare."""
    lines = await lines_of_reseller(session, reseller_id)
    if not lines:
        return None, None
    buttons = [
        [
            InlineKeyboardButton(
                f"{ui.line_title(line)} · {line.expires_on:%d/%m}",
                callback_data=f"rq:{line.id}",
            )
        ]
        for line in lines[:25]
    ]
    buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    body = f"🔁 <b>Richiedi rinnovo</b>\n\nScegli la linea ({len(lines)})."
    return body, InlineKeyboardMarkup(buttons)


async def ask_which_line(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Il rivenditore sceglie quale delle sue linee rinnovare."""
    query = update.callback_query
    if query is not None:
        await query.answer()
        target = query.edit_message_text
    else:
        message = update.effective_message
        if message is None:
            return
        target = ui.html_target(message)

    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.allowed:
            await target(texts.UNAUTHORIZED.format(user_id=user.id))
            return
        if access.is_owner:
            await target(
                "La richiesta di rinnovo serve ai rivenditori: come owner rinnovi "
                "direttamente dalla scheda della linea."
            )
            return
        body, markup = await picker_payload(session, access.reseller_id or 0)

    if body is None:
        await target("Non hai linee attive collegate al tuo account.")
        return
    await target(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def pick_months(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    _, raw_id = query.data.split(":")
    line_id = int(raw_id)

    presets = _presets()
    buttons = [
        InlineKeyboardButton(f"{months} m", callback_data=f"rqm:{line_id}:{months}")
        for months in presets
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("Altro…", callback_data=f"rqm:{line_id}:other")])
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="menu:request")])

    await query.edit_message_text(
        "Quanti mesi vuoi richiedere?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, raw_id, raw_months = query.data.split(":")

    if raw_months == "other":
        await query.answer()
        context.user_data["awaiting"] = f"reqmonths:{raw_id}"
        await query.edit_message_text(
            "Quanti mesi? Scrivi un numero da 1 a 36.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("⬅️ Indietro", callback_data="menu:request")]]
            ),
        )
        return

    await query.answer()
    await _submit(update, context, int(raw_id), int(raw_months), editing=ui.Editor(query))


async def submit_from_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, line_id: int, months: int
) -> bool:
    message = update.effective_message
    if message is None:
        return True
    await _submit(update, context, line_id, months, editing=ui.Editor(message))
    return True


async def _submit(update, context, line_id: int, months: int, *, editing) -> None:
    user = update.effective_user
    chat = update.effective_chat
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_reseller or not access.can_act:
            await editing.edit_text(
                "Solo il rivenditore collegato può inviare richieste: scrivi al bot in privato."
            )
            return
        line = await get_line(session, line_id)
        if line is None or line.reseller_id != access.reseller_id:
            await editing.edit_text("Linea non disponibile.")
            return

        try:
            request = await create_request(
                session,
                line_id=line.id,
                requested_by=user.id,
                months=months,
                origin_chat_id=chat.id if chat else None,
            )
        except RequestError as exc:
            await editing.edit_text(f"Richiesta non inviata: {exc}")
            return

        title = ui.line_title(line)
        expires = line.expires_on
        price = line.monthly_price_cents
        total = price * months

    context.user_data.pop("awaiting", None)
    await editing.edit_text(
        f"✅ <b>Richiesta inviata</b>\n\n{ui.esc(title)}\n"
        f"{months} {_period(months)} · scade {ui.italian_date(expires)}\n"
        "Ti avviso appena l'owner risponde.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]
        ),
    )

    owner_id = get_settings().owner_telegram_id
    if owner_id is not None and context.bot is not None:
        presets = _presets()
        buttons = [
            InlineKeyboardButton(f"{choice} m", callback_data=f"rqa:{request.id}:{choice}")
            for choice in presets
        ]
        rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
        rows.append([InlineKeyboardButton("🚫 Rifiuta", callback_data=f"rqr:{request.id}")])
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    "🔔 <b>Richiesta di rinnovo</b>\n\n"
                    f"{ui.esc(access.name)} · {ui.esc(title)}\n"
                    f"Scade {ui.italian_date(expires)} · richiesti {months} {_period(months)}\n"
                    f"Prezzo {format_eur(price)}/mese · totale {format_eur(total)}"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(rows),
            )
        except Exception:
            logger.exception("Richiesta %s: invio all'owner fallito", request.id)


async def _announce_outcome(
    context,
    *,
    request_id: int,
    text: str,
    requester: int | None,
    origin: int | None,
) -> None:
    """Esito al gruppo da cui è partita la richiesta, e al richiedente.

    Se la richiesta è nata in un gruppo l'esito va **nel gruppo**: è lì che il
    rivenditore l'ha chiesta e lì che deve vederlo. Il privato al richiedente
    serve solo se la richiesta è nata in privato o se chi ha premuto non è
    l'owner (che l'esito lo ha già davanti).
    """
    if context.bot is None:
        return

    targets: list[int] = []
    if origin is not None:
        targets.append(origin)
    if requester and requester not in targets:
        owner_id = get_settings().owner_telegram_id
        if origin is None or requester != owner_id:
            targets.append(requester)

    for chat_id in targets:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        except Exception:
            logger.warning("Richiesta %s: esito non consegnato a %s", request_id, chat_id)


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, raw_id, raw_months = query.data.split(":")
    request_id = int(raw_id)
    months = int(raw_months)

    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await query.answer("Solo l'owner può approvare.", show_alert=True)
            return
        try:
            request, line, renewal = await approve_request(
                session,
                request_id=request_id,
                months=months,
                today=_today(),
                approved_by=user.id,
            )
        except RequestError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        title = ui.line_title(line)
        expires = line.expires_on
        amount = renewal.total_cents
        requester = request.requested_by_telegram_id
        origin = request.origin_chat_id

    await query.answer("Rinnovo registrato")
    await query.edit_message_text(
        f"✅ <b>Rinnovo approvato</b>\n\n{ui.esc(title)}\n"
        f"{months} {_period(months)} · nuova scadenza {ui.italian_date(expires)}\n"
        f"Importo: <b>{format_eur(amount)}</b>",
        parse_mode=ParseMode.HTML,
    )

    await _announce_outcome(
        context,
        request_id=request_id,
        text=(
            "✅ <b>Rinnovo approvato</b>\n\n"
            f"{ui.esc(title)}\n{months} {_period(months)} · "
            f"nuova scadenza {ui.italian_date(expires)}\n"
            f"Importo: <b>{format_eur(amount)}</b>"
        ),
        requester=requester,
        origin=origin,
    )


async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, raw_id = query.data.split(":")
    request_id = int(raw_id)

    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await query.answer("Solo l'owner può rifiutare.", show_alert=True)
            return
        try:
            request = await reject_request(session, request_id=request_id, rejected_by=user.id)
        except RequestError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        line = await get_line(session, request.line_id)
        title = ui.line_title(line) if line is not None else f"linea {request.line_id}"
        requester = request.requested_by_telegram_id
        origin = request.origin_chat_id

    await query.answer("Richiesta rifiutata")
    await query.edit_message_text(
        f"🚫 <b>Richiesta rifiutata</b>\n\n{ui.esc(title)}",
        parse_mode=ParseMode.HTML,
    )
    await _announce_outcome(
        context,
        request_id=request_id,
        text=(
            "🚫 <b>Richiesta rifiutata</b>\n\n"
            f"{ui.esc(title)}\n<i>La linea resta com'è: nessun rinnovo registrato.</i>"
        ),
        requester=requester,
        origin=origin,
    )


async def authorize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """L'owner abilita un Telegram ID sconosciuto che ha scritto al bot."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    _, raw_user, action = query.data.split(":")
    telegram_user_id = int(raw_user)

    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await query.answer("Solo l'owner può autorizzare.", show_alert=True)
            return
        if action == "no":
            await query.edit_message_text("Autorizzazione ignorata.")
            return
        name = context.user_data.get(f"pending_name:{telegram_user_id}") or ""
        reseller = await authorize_reseller(session, telegram_user_id=telegram_user_id, name=name)
        name = reseller.name

    await query.answer("Autorizzato")
    await query.edit_message_text(
        f"✅ <b>{ui.esc(name)}</b> autorizzato come rivenditore.\n"
        "Ora può scrivere al bot e ricevere le sue scadenze.",
        parse_mode=ParseMode.HTML,
    )
    if context.bot is not None:
        try:
            await context.bot.send_message(
                chat_id=telegram_user_id,
                text="✅ Sei stato autorizzato. Scrivi /start per aprire il menu.",
            )
        except Exception:
            logger.warning("Autorizzazione %s: avviso all'utente fallito", telegram_user_id)


async def pending_for_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elenco delle richieste in attesa: rinnovi e nuove linee (owner)."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    from app.models import Reseller, Service
    from app.services.line_requests import pending_new_line_requests
    from app.services.requests import pending_requests

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await message.reply_html(texts.UNAUTHORIZED.format(user_id=user.id))
            return
        requests = await pending_requests(session)
        new_lines = await pending_new_line_requests(session)
        rows: list[str] = []
        buttons: list[list[InlineKeyboardButton]] = []

        if requests:
            rows.append("<b>Rinnovi</b>")
            for request in requests:
                line = await get_line(session, request.line_id)
                if line is None:
                    continue
                period = "mese" if request.months == 1 else "mesi"
                rows.append(
                    f"• rinnovo #{request.id} {ui.esc(ui.line_title(line))} — "
                    f"{request.months} {period} "
                    f"da {request.requested_by_telegram_id}"
                )
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"✅ Approva · {ui.line_title(line)[:30]} · {request.months} {period}",
                            callback_data=f"rqa:{request.id}:{request.months}",
                        )
                    ]
                )
                buttons.append(
                    [InlineKeyboardButton("✖️ Rifiuta", callback_data=f"rqr:{request.id}")]
                )

        if new_lines:
            if rows:
                rows.append("")
            rows.append("<b>Nuove linee</b>")
            for request in new_lines:
                reseller = await session.get(Reseller, request.reseller_id)
                service = "Live + VOD" if request.service is Service.LIVE_VOD else "Live"
                period = "mese" if request.months == 1 else "mesi"
                rows.append(
                    f"• nuova linea #{request.id} {ui.esc(request.name)} — "
                    f"{request.months} {period} · {service} · "
                    f"da {ui.esc(reseller.name if reseller else '?')}"
                )
                buttons.append(
                    [
                        InlineKeyboardButton(
                            f"✅ Conferma · {request.name[:30]}",
                            callback_data=f"nla:{request.id}",
                        )
                    ]
                )
                buttons.append(
                    [InlineKeyboardButton("✖️ Rifiuta", callback_data=f"nlx:{request.id}")]
                )

    body = ["🔔 <b>Richieste in attesa</b>", ""]
    body.extend(rows or ["Nessuna richiesta in attesa."])
    await message.reply_html(
        "\n".join(body),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None,
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    kind = query.data.split(":", 1)[0]
    if kind == "rq":
        await pick_months(update, context)
    elif kind == "rqm":
        await confirm(update, context)
    elif kind == "rqa":
        await approve(update, context)
    elif kind == "rqr":
        await reject(update, context)
    elif kind == "auth":
        await authorize(update, context)


async def handle_request_text(update, context, pending: str) -> bool:
    """Mesi richiesti a mano: 'reqmonths:<line_id>'."""
    kind, _, raw_id = pending.partition(":")
    if kind != "reqmonths" or not raw_id.isdigit():
        return False
    message = update.effective_message
    if message is None or message.text is None:
        return True
    text = message.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 36:
        await message.reply_html("Scrivi un numero di mesi da 1 a 36.")
        return True
    await submit_from_text(update, context, int(raw_id), int(text))
    return True


async def collega(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collega il gruppo corrente a un rivenditore.

    Il rivenditore lo fa per sé nel proprio gruppo; l'owner indica il nome.
    """
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if chat is None or message is None or user is None:
        return
    if not is_group(chat.type):
        await message.reply_html(
            "Usa /collega dentro il gruppo del rivenditore: in privato non serve."
        )
        return

    from app.models import Reseller
    from app.services.requests import link_group, reseller_by_name

    async with session_scope() as session:
        access = await access_for(update, session)
        if access.is_owner:
            name = " ".join(context.args or []).strip()
            if not name:
                await message.reply_html(
                    "Scrivi <code>/collega Nome</code>, per esempio <code>/collega Mario</code>."
                )
                return
            reseller = await reseller_by_name(session, name)
            if reseller is None:
                await message.reply_html(f"Rivenditore «{ui.esc(name)}» non trovato.")
                return
        elif access.is_reseller and access.reseller_id is not None:
            reseller = await session.get(Reseller, access.reseller_id)
        else:
            await message.reply_html(texts.UNAUTHORIZED.format(user_id=user.id))
            return

        if reseller is None:
            await message.reply_html("Rivenditore non trovato.")
            return
        linked = await link_group(
            session,
            reseller_id=reseller.id,
            chat_id=chat.id,
            title=chat.title,
        )
        name = linked.name

    await message.reply_html(
        f"✅ Gruppo collegato a <b>{ui.esc(name)}</b>.\n\n"
        "Qui bastano i comandi indirizzati: <code>/scadenze@bot</code> e "
        "<code>/richiedi@bot</code>. I prezzi e le modifiche restano in privato.",
    )


async def scollega(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toglie il collegamento fra il gruppo corrente e il rivenditore."""
    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if chat is None or message is None:
        return
    if not is_group(chat.type):
        await message.reply_html("Questo comando si usa nel gruppo del rivenditore.")
        return

    from app.services.requests import unlink_group

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.can_act:
            await message.reply_html(texts.UNAUTHORIZED.format(user_id=user.id if user else "?"))
            return
        reseller = await unlink_group(session, chat_id=chat.id)

    if reseller is None:
        await message.reply_html("Questo gruppo non era collegato a nessun rivenditore.")
        return
    await message.reply_html(f"Gruppo scollegato da <b>{ui.esc(reseller.name)}</b>.")


async def request_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """In gruppo la richiesta riparte in privato. True se gestita qui."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None or not is_group(chat.type):
        return False

    from app.models import Reseller

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_reseller or access.reseller_id is None:
            await message.reply_html(
                "Le richieste di rinnovo le manda il rivenditore dal proprio account."
            )
            return True
        reseller = await session.get(Reseller, access.reseller_id)
        if reseller is None or reseller.telegram_user_id is None:
            await message.reply_html("Manca il tuo ID Telegram: chiedi all'owner di collegarlo.")
            return True
        body, markup = await picker_payload(session, reseller.id)
        telegram_user_id = reseller.telegram_user_id

    if body is None:
        await message.reply_html("Non ci sono linee attive da rinnovare.")
        return True

    try:
        await context.bot.send_message(
            chat_id=telegram_user_id,
            text=body,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        await message.reply_html("🔒 Ti ho scritto in privato con le tue linee.")
    except Exception:
        logger.exception("Richiesta in gruppo: DM al rivenditore fallito")
        await message.reply_html("Non riesco a scriverti in privato: apri il bot e premi /start.")
    return True
