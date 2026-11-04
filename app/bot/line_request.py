"""Richiesta di una nuova linea da parte del rivenditore.

Il rivenditore compila nome, mesi, servizio e nota; l'owner riceve la richiesta
e decide. Solo alla conferma nascono cliente e linea, con l'ID congelato al
giorno della richiesta (`nome` + `GGMMAA`) e un blocco da copiare nel pannello
interamente in monospace.

Il flusso vive sia in privato sia nel gruppo del rivenditore: in gruppo lo
stato sta in `chat_data`, così non si mescola con quello personale.
"""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for, is_group
from app.config import get_settings
from app.db import session_scope
from app.models import NewLineRequest, RequestStatus, Reseller, Service
from app.services.line_requests import (
    NAME_MAX,
    NewLineRequestError,
    approve_new_line_request,
    create_new_line_request,
    reject_new_line_request,
)
from app.services.months import MAX_MONTHS, MIN_MONTHS
from app.services.text import format_eur, parse_price_to_cents

logger = logging.getLogger(__name__)

STATE_KEY = "new_line_request"
OWNER_PRICE_PREFIX = "nlprice"


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def _in_group(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and is_group(chat.type)


def _store(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict:
    if _in_group(update):
        data = getattr(context, "chat_data", None)
        if data is not None:
            return data
    return context.user_data


def _state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    return _store(update, context).get(STATE_KEY)


def _reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _store(update, context).pop(STATE_KEY, None)


def _editor(update: Update) -> ui.Editor:
    query = update.callback_query
    if query is not None:
        return ui.Editor(query)
    return ui.Editor(update.effective_message)


def _cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("✖️ Annulla", callback_data="nlr:cancel")]


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


def _service_label(value: str) -> str:
    return "Live + VOD" if value == Service.LIVE_VOD else "Live"


def _months_word(months: int) -> str:
    return "mese" if months == 1 else "mesi"


def _name_prompt(*, group: bool) -> str:
    body = (
        "🆕 <b>Nuova linea</b>\n\n"
        "Come si chiama il cliente? (nome e cognome)\n"
        "Il nome finisce nell'ID, seguito dal giorno, dal mese e dall'anno "
        "della richiesta a due cifre."
    )
    if group:
        body += (
            "\n\n<i>Rispondi a questo messaggio</i>: così il bot legge il nome "
            "anche con la privacy attiva."
        )
    return body


def _reply_hint(update: Update) -> str:
    if not _in_group(update):
        return ""
    return (
        "\n\n<i>Rispondi a questo messaggio</i>: così il bot legge il testo "
        "anche con la privacy attiva."
    )


def _months_keyboard() -> InlineKeyboardMarkup:
    presets = get_settings().duration_presets_list
    buttons = [
        InlineKeyboardButton(f"{months}", callback_data=f"nlr:m:{months}") for months in presets
    ]
    rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton("Altro…", callback_data="nlr:m:other")])
    rows.append(_cancel_row())
    return InlineKeyboardMarkup(rows)


def _service_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Live", callback_data="nlr:s:live"),
                InlineKeyboardButton("Live + VOD", callback_data="nlr:s:live_vod"),
            ],
            _cancel_row(),
        ]
    )


def _note_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⏭ Salta", callback_data="nlr:n:skip")],
            _cancel_row(),
        ]
    )


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📨 Invia richiesta", callback_data="nlr:send")],
            _cancel_row(),
        ]
    )


def _summary(state: dict) -> str:
    note = state.get("note") or "—"
    return (
        "🆕 <b>Riepilogo</b>\n\n"
        f"Cliente: <b>{ui.esc(state['name'])}</b>\n"
        f"Servizio: {_service_label(state['service'])}\n"
        f"Durata: {state['months']} {_months_word(state['months'])}\n"
        f"Nota: {ui.esc(note)}\n\n"
        "<i>L'ID viene generato all'invio con la data di oggi.</i>"
    )


async def start_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pulsante nel menu privato del rivenditore."""
    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)

    editing = _editor(update)
    if not access.is_reseller or not access.can_act or access.reseller_id is None:
        await editing.edit_text(
            "Le richieste di nuove linee le manda il rivenditore dal proprio account.\n"
            "L'owner usa «🆕 Nuovo cliente»."
        )
        return

    context.user_data[STATE_KEY] = {"step": "name", "reseller_id": access.reseller_id}
    await editing.edit_text(
        _name_prompt(group=False),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([_cancel_row()]),
    )


async def start_group(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, reseller_id: int
) -> None:
    """Pulsante nel menu del gruppo collegato: il flusso resta nel gruppo."""
    chat = update.effective_chat
    if chat is None:
        return
    _store(update, context)[STATE_KEY] = {
        "step": "name",
        "reseller_id": reseller_id,
        "origin_chat_id": chat.id,
    }
    await ui.Editor(update.callback_query).edit_text(
        _name_prompt(group=True),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([_cancel_row()]),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    editing = ui.Editor(query)

    if action == "cancel":
        _reset(update, context)
        await editing.edit_text("✖️ Richiesta annullata: non ho inviato niente.")
        return

    state = _state(update, context)
    if state is None:
        await editing.edit_text("Il flusso è scaduto: premi di nuovo «🆕 Nuova linea».")
        return

    if action == "m":
        if "name" not in state:
            await editing.edit_text("Il flusso è incompleto: ricomincia da «🆕 Nuova linea».")
            return
        raw = parts[2] if len(parts) > 2 else ""
        if raw == "other":
            state["step"] = "months_text"
            await editing.edit_text(
                f"Quanti mesi? Scrivi un numero da {MIN_MONTHS} a {MAX_MONTHS}."
                f"{_reply_hint(update)}",
                reply_markup=InlineKeyboardMarkup([_cancel_row()]),
            )
            return
        if not raw.isdigit() or not MIN_MONTHS <= int(raw) <= MAX_MONTHS:
            await editing.edit_text(f"Il periodo deve stare tra {MIN_MONTHS} e {MAX_MONTHS} mesi.")
            return
        state["months"] = int(raw)
        state["step"] = "service"
        await editing.edit_text(
            f"🆕 <b>{ui.esc(state['name'])}</b>\n\n"
            f"{state['months']} {_months_word(state['months'])}.\n"
            "Quale servizio?",
            parse_mode=ParseMode.HTML,
            reply_markup=_service_keyboard(),
        )
        return

    if action == "s":
        if "name" not in state or "months" not in state:
            await editing.edit_text("Il flusso è incompleto: ricomincia da «🆕 Nuova linea».")
            return
        raw = parts[2] if len(parts) > 2 else ""
        if raw not in {"live", "live_vod"}:
            await editing.edit_text("Scegli Live oppure Live + VOD.")
            return
        state["service"] = raw
        state["step"] = "note"
        await editing.edit_text(
            f"🆕 <b>{ui.esc(state['name'])}</b>\n\n"
            "Una nota per riconoscere il cliente? Finisce identica nel pannello.\n"
            "Puoi saltarla."
            f"{_reply_hint(update)}",
            parse_mode=ParseMode.HTML,
            reply_markup=_note_keyboard(),
        )
        return

    if action == "n":
        if "service" not in state:
            await editing.edit_text("Il flusso è incompleto: ricomincia da «🆕 Nuova linea».")
            return
        if len(parts) > 2 and parts[2] == "skip":
            state["note"] = None
        state["step"] = "confirm"
        await editing.edit_text(
            _summary(state),
            parse_mode=ParseMode.HTML,
            reply_markup=_confirm_keyboard(),
        )
        return

    if action == "send":
        await _send(update, context, state)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Passi a testo libero del flusso. True se il messaggio è stato consumato."""
    state = _state(update, context)
    message = update.effective_message
    if not state or message is None or message.text is None:
        return False

    step = state.get("step")
    text = message.text.strip()

    if step == "name":
        if len(text) < 2:
            await message.reply_html("Scrivi nome e cognome del cliente.")
            return True
        if len(text) > NAME_MAX:
            await message.reply_html(f"Il nome supera {NAME_MAX} caratteri: accorcialo.")
            return True
        state["name"] = _clean(text)
        state["step"] = "months"
        await message.reply_html(
            f"🆕 <b>{ui.esc(state['name'])}</b>\n\nQuanti mesi?",
            reply_markup=_months_keyboard(),
        )
        return True

    if step == "months_text":
        if not text.isdigit() or not MIN_MONTHS <= int(text) <= MAX_MONTHS:
            await message.reply_html(f"Scrivi un numero di mesi tra {MIN_MONTHS} e {MAX_MONTHS}.")
            return True
        state["months"] = int(text)
        state["step"] = "service"
        await message.reply_html(
            f"🆕 <b>{ui.esc(state['name'])}</b>\n\n"
            f"{state['months']} {_months_word(state['months'])}.\n"
            "Quale servizio?",
            reply_markup=_service_keyboard(),
        )
        return True

    if step == "note":
        state["note"] = _clean(text)
        state["step"] = "confirm"
        await message.reply_html(_summary(state), reply_markup=_confirm_keyboard())
        return True

    if step in {"months", "service", "confirm"}:
        await message.reply_html("Usa i pulsanti qui sopra, oppure «✖️ Annulla» per ricominciare.")
        return True

    return False


async def _send(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> None:
    query = update.callback_query
    user = update.effective_user
    chat = update.effective_chat
    editing = ui.Editor(query)
    if user is None or chat is None:
        return
    if not all(key in state for key in ("name", "months", "service")):
        await editing.edit_text("Dati incompleti: ricomincia da «🆕 Nuova linea».")
        _reset(update, context)
        return

    service = Service.LIVE_VOD if state["service"] == "live_vod" else Service.LIVE
    today = _today()

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_reseller or access.reseller_id is None:
            await editing.edit_text(
                "Le richieste di nuove linee le manda il rivenditore dal proprio account."
            )
            return
        try:
            request = await create_new_line_request(
                session,
                reseller_id=access.reseller_id,
                requested_by=user.id,
                name=state["name"],
                months=state["months"],
                service=service,
                note=state.get("note"),
                today=today,
                origin_chat_id=chat.id,
                origin_message_id=query.message.message_id if query.message else None,
            )
        except NewLineRequestError as exc:
            await editing.edit_text(f"Richiesta non inviata: {exc}")
            return
        reseller = await session.get(Reseller, request.reseller_id)
        reseller_name = reseller.name if reseller is not None else "rivenditore"
        payload = {
            "request_id": request.id,
            "name": request.name,
            "username": request.username,
            "months": request.months,
            "service": request.service,
            "note": request.note,
            "reseller_name": reseller_name,
            "group_title": chat.title,
        }

    _reset(update, context)
    await editing.edit_text(
        "✅ <b>Richiesta inviata</b>\n\n"
        f"{ui.esc(payload['name'])} · {_service_label(payload['service'])}\n"
        f"{payload['months']} {_months_word(payload['months'])}\n"
        "Ti avviso appena l'owner risponde.",
        parse_mode=ParseMode.HTML,
    )
    await _notify_owner(context, **payload)


def _owner_text(**payload) -> str:
    note = payload["note"] or "—"
    where = f"gruppo «{ui.esc(payload['group_title'])}»" if payload["group_title"] else "in privato"
    return (
        f"🆕 <b>Il rivenditore {ui.esc(payload['reseller_name'])} "
        "chiede una nuova linea</b>\n\n"
        f"Cliente: <b>{ui.esc(payload['name'])}</b>\n"
        f"Servizio: {_service_label(payload['service'])}\n"
        f"Durata: {payload['months']} {_months_word(payload['months'])}\n"
        f"ID: <code>{ui.esc(payload['username'])}</code>\n"
        f"Nota: <code>{ui.esc(note)}</code>\n"
        f"Da: {where}"
    )


async def _notify_owner(context: ContextTypes.DEFAULT_TYPE, **payload) -> None:
    owner = get_settings().owner_telegram_id
    if owner is None or context.bot is None:
        return
    try:
        await context.bot.send_message(
            chat_id=owner,
            text=_owner_text(**payload),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Conferma",
                            callback_data=f"nla:{payload['request_id']}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "✖️ Rifiuta",
                            callback_data=f"nlx:{payload['request_id']}",
                        )
                    ],
                ]
            ),
        )
    except Exception:
        logger.exception(
            "Richiesta nuova linea %s: avviso all'owner non riuscito",
            payload["request_id"],
        )


async def on_owner_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if query is None or query.data is None or user is None:
        return

    parts = query.data.split(":")
    kind = parts[0]
    request_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    if request_id is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
    if not access.is_owner:
        await query.answer("Solo l'owner può confermare.", show_alert=True)
        return

    if kind == "nla":
        await query.answer()
        await _ask_price(update, context, request_id)
        return

    if kind == "nlp":
        raw = parts[2] if len(parts) > 2 else ""
        if raw == "other":
            await query.answer()
            context.user_data["awaiting"] = f"{OWNER_PRICE_PREFIX}:{request_id}"
            await query.edit_message_text(
                "💶 Prezzo mensile? Scrivilo in euro (es. <code>7</code> o <code>7,50</code>).",
                parse_mode=ParseMode.HTML,
            )
            return
        if raw == "default":
            cents = await _reseller_default_price(request_id)
            if cents is None:
                await query.answer("Nessun prezzo predefinito.", show_alert=True)
                return
        elif raw.isdigit():
            cents = int(raw)
        else:
            return
        await query.answer()
        await _approve(update, context, request_id, cents, editing=ui.Editor(query))
        return

    if kind == "nlx":
        await query.answer()
        await _reject(update, context, request_id, editing=ui.Editor(query))


async def _reseller_default_price(request_id: int) -> int | None:
    async with session_scope() as session:
        request = await session.get(NewLineRequest, request_id)
        if request is None:
            return None
        reseller = await session.get(Reseller, request.reseller_id)
        if reseller is None:
            return None
        return reseller.default_monthly_price_cents


async def _ask_price(update: Update, context: ContextTypes.DEFAULT_TYPE, request_id: int) -> None:
    editing = _editor(update)
    async with session_scope() as session:
        request = await session.get(NewLineRequest, request_id)
        if request is None or request.status is not RequestStatus.PENDING:
            await editing.edit_text("Richiesta non trovata o già gestita.")
            return
        reseller = await session.get(Reseller, request.reseller_id)
        default = reseller.default_monthly_price_cents if reseller is not None else None
        name = request.name
        service = request.service
        months = request.months
        note = request.note

    presets = list(get_settings().direct_price_presets_cents)
    if default is not None:
        presets = [default] + [cents for cents in presets if cents != default]

    buttons = [
        InlineKeyboardButton(
            f"{format_eur(cents)}{' (default)' if cents == default else ''}",
            callback_data=f"nlp:{request_id}:default"
            if cents == default
            else f"nlp:{request_id}:{cents}",
        )
        for cents in presets
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("Altro…", callback_data=f"nlp:{request_id}:other")])

    note_line = f"Nota: <code>{ui.esc(note)}</code>\n" if note else ""
    await editing.edit_text(
        "💰 <b>Prezzo mensile</b>\n\n"
        f"Cliente: <b>{ui.esc(name)}</b>\n"
        f"Servizio: {_service_label(service)}\n"
        f"Durata: {months} {_months_word(months)}\n"
        f"{note_line}"
        "\nScegli un preset o scrivilo.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def _approve(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    request_id: int,
    price_cents: int,
    *,
    editing: ui.Editor,
) -> None:
    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await editing.edit_text("Solo l'owner può confermare.")
            return
        try:
            request, customer, line, username, password = await approve_new_line_request(
                session,
                request_id=request_id,
                price_cents=price_cents,
                approved_by=user.id,
                today=_today(),
            )
        except NewLineRequestError as exc:
            await editing.edit_text(f"Non confermata: {exc}")
            return
        reseller = await session.get(Reseller, request.reseller_id)
        payload = {
            "name": customer.name,
            "reseller_name": reseller.name if reseller is not None else "—",
            "service": request.service,
            "months": request.months,
            "username": username,
            "password": password,
            "note": request.note,
            "price_cents": line.monthly_price_cents,
            "expires_on": line.expires_on,
            "origin_chat_id": request.origin_chat_id,
            "requested_by": request.requested_by_telegram_id,
        }

    await editing.edit_text(_created_text(**payload), parse_mode=ParseMode.HTML)
    await _announce(context, payload=payload)


def _created_text(**payload) -> str:
    lines = [
        f"SERVIZIO: {_service_label(payload['service'])}",
        f"ID: {payload['username']}",
        f"PASSWORD: {payload['password']}",
        f"DURATA: {payload['months']} {_months_word(payload['months'])}",
    ]
    if payload["note"]:
        lines.append(f"NOTE: {payload['note']}")

    block = "\n".join(ui.esc(line) for line in lines)
    return (
        "✅ <b>Nuova linea confermata</b>\n\n"
        f"Cliente: <b>{ui.esc(payload['name'])}</b>\n"
        f"Rivenditore: {ui.esc(payload['reseller_name'])}\n"
        f"Prezzo: {format_eur(payload['price_cents'])}/mese · "
        f"Scadenza: {ui.italian_date(payload['expires_on'])}\n\n"
        "<pre>"
        f"{block}"
        "</pre>\n\n"
        "<i>Tutto quello che serve al pannello è qui sopra: copia e incolla "
        "ID, password e nota. La linea resta «da creare nel pannello» finché "
        "il sync non la trova.</i>"
    )


async def _announce(context: ContextTypes.DEFAULT_TYPE, *, payload: dict) -> None:
    target = payload["origin_chat_id"] or payload["requested_by"]
    if target is None or context.bot is None:
        return
    try:
        await context.bot.send_message(
            chat_id=target,
            text=_created_text(**payload),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.warning("Nuova linea: esito non consegnato a %s", target)


async def _reject(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    request_id: int,
    *,
    editing: ui.Editor,
) -> None:
    user = update.effective_user
    if user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await editing.edit_text("Solo l'owner può rifiutare.")
            return
        try:
            request = await reject_new_line_request(
                session, request_id=request_id, rejected_by=user.id
            )
        except NewLineRequestError as exc:
            await editing.edit_text(f"Non rifiutata: {exc}")
            return
        target = request.origin_chat_id or request.requested_by_telegram_id
        name = request.name

    await editing.edit_text(
        f"🚫 <b>Richiesta rifiutata</b>\n\n{ui.esc(name)}",
        parse_mode=ParseMode.HTML,
    )
    if target is None or context.bot is None:
        return
    try:
        await context.bot.send_message(
            chat_id=target,
            text=(
                "🚫 <b>Richiesta di nuova linea rifiutata</b>\n\n"
                f"{ui.esc(name)}\n"
                "<i>Non è stato creato niente.</i>"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.warning("Nuova linea %s: rifiuto non consegnato a %s", request_id, target)


async def handle_owner_price_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: str
) -> bool:
    """Prezzo scritto a mano dall'owner: 'nlprice:<request_id>'."""
    kind, _, raw_id = pending.partition(":")
    if kind != OWNER_PRICE_PREFIX or not raw_id.isdigit():
        return False

    message = update.effective_message
    if message is None or message.text is None:
        return True
    cents = parse_price_to_cents(message.text.strip())
    if cents is None:
        await message.reply_html("Prezzo non valido: scrivi per esempio <code>7</code>.")
        return True

    context.user_data.pop("awaiting", None)
    await _approve(
        update,
        context,
        int(raw_id),
        cents,
        editing=ui.Editor(message),
    )
    return True
