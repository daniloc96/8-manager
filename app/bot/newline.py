from __future__ import annotations

import logging
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.config import get_settings
from app.db import session_scope
from app.models import Reseller, Service, Setting
from app.services.lines import LineDraft, create_customer_and_line
from app.services.months import MAX_MONTHS, MIN_MONTHS
from app.services.text import format_eur, normalize_key, parse_price_to_cents

logger = logging.getLogger(__name__)

ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


def _state(context) -> dict:
    return context.user_data.setdefault("new_line", {})


def _cancel_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("✖️ Annulla", callback_data="new:cancel")]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    async with session_scope() as session:
        access = await access_for(update, session)
    if not access.is_owner:
        await message.reply_html("Solo l'owner può creare clienti.")
        return

    context.user_data["new_line"] = {"step": "name"}
    await message.reply_html(
        "🆕 <b>Nuovo cliente</b>\n\nCome si chiama? (nome e cognome)",
        reply_markup=InlineKeyboardMarkup([_cancel_row()]),
    )


async def _ask_service(query, state) -> None:
    await query.edit_message_text(
        f"🆕 <b>{ui.esc(state['name'])}</b>\n\nQuale servizio?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Live", callback_data="new:service:live"),
                    InlineKeyboardButton("Live + VOD", callback_data="new:service:live_vod"),
                ],
                _cancel_row(),
            ]
        ),
    )


async def _ask_reseller(query, state) -> None:
    async with session_scope() as session:
        resellers = list(
            (await session.scalars(select(Reseller).where(Reseller.active.is_(True)))).all()
        )

    rows = [[InlineKeyboardButton("Diretto (io)", callback_data="new:reseller:0")]]
    for reseller in resellers:
        rows.append(
            [InlineKeyboardButton(reseller.name, callback_data=f"new:reseller:{reseller.id}")]
        )
    rows.append(_cancel_row())
    await query.edit_message_text(
        f"🆕 <b>{ui.esc(state['name'])}</b>\n\nA chi va la linea?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _ask_price(state) -> tuple[str, InlineKeyboardMarkup]:
    presets = get_settings().direct_price_presets_cents
    buttons = [
        InlineKeyboardButton(format_eur(cents), callback_data=f"new:price:{cents}")
        for cents in presets
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("Altro…", callback_data="new:price:other")])
    rows.append(_cancel_row())
    body = f"🆕 <b>{ui.esc(state['name'])}</b>\n\nPrezzo mensile? Scegli un preset o scrivilo."
    return body, InlineKeyboardMarkup(rows)


def _ask_months(state) -> tuple[str, InlineKeyboardMarkup]:
    presets = get_settings().duration_presets_list
    buttons = [
        InlineKeyboardButton(f"{months} m", callback_data=f"new:months:{months}")
        for months in presets
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("Altro…", callback_data="new:months:other")])
    rows.append(_cancel_row())
    body = f"🆕 <b>{ui.esc(state['name'])}</b>\n\nDurata iniziale?"
    return body, InlineKeyboardMarkup(rows)


def _summary(state) -> tuple[str, InlineKeyboardMarkup]:
    service = "Live + VOD" if state["service"] == "live_vod" else "Live"
    who = state.get("reseller_name") or "Diretto"
    body = (
        "🆕 <b>Riepilogo</b>\n\n"
        f"Cliente: <b>{ui.esc(state['name'])}</b>\n"
        f"Servizio: {service}\n"
        f"Rivenditore: {ui.esc(who)}\n"
        f"Prezzo: {format_eur(state['price'])}/mese\n"
        f"Durata: {state['months']} mesi"
    )
    return body, InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Crea", callback_data="new:create")],
            _cancel_row(),
        ]
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    user = update.effective_user
    if user is None:
        return
    async with session_scope() as session:
        access = await access_for(update, session)
    if not access.is_owner:
        await query.edit_message_text("Solo l'owner può creare clienti.")
        return

    data = query.data
    if data == "new:cancel":
        context.user_data.pop("new_line", None)
        await query.edit_message_text("Creazione annullata.")
        return

    state = _state(context)
    action = data.split(":", 1)[1]

    if action.startswith("service:"):
        state["service"] = action.split(":", 1)[1]
        state["step"] = "reseller"
        await _ask_reseller(query, state)
        return

    if action.startswith("reseller:"):
        raw = action.split(":", 1)[1]
        state["reseller_id"] = int(raw) or None
        state["reseller_name"] = None
        if state["reseller_id"] is not None:
            async with session_scope() as session:
                reseller = await session.get(Reseller, state["reseller_id"])
                state["reseller_name"] = reseller.name if reseller else None
        state["step"] = "price"
        body, markup = _ask_price(state)
        await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if action.startswith("price:"):
        raw = action.split(":", 1)[1]
        if raw == "other":
            state["step"] = "price_text"
            await query.edit_message_text(
                "Scrivi il prezzo mensile in euro (es. <code>5</code> o <code>5,50</code>).",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([_cancel_row()]),
            )
            return
        state["price"] = int(raw)
        state["step"] = "months"
        body, markup = _ask_months(state)
        await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if action.startswith("months:"):
        raw = action.split(":", 1)[1]
        if raw == "other":
            state["step"] = "months_text"
            await query.edit_message_text(
                "Scrivi il numero di mesi (1-36).",
                reply_markup=InlineKeyboardMarkup([_cancel_row()]),
            )
            return
        state["months"] = int(raw)
        state["step"] = "confirm"
        body, markup = _summary(state)
        await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)
        return

    if action == "create":
        await _create(update, context, query)


async def _create(update: Update, context: ContextTypes.DEFAULT_TYPE, query) -> None:
    state = _state(context)
    if not all(key in state for key in ("name", "service", "price", "months")):
        await query.edit_message_text("Dati incompleti: ricomincia con /nuovo.")
        context.user_data.pop("new_line", None)
        return

    today = _today()
    service = Service.LIVE_VOD if state["service"] == "live_vod" else Service.LIVE

    async with session_scope() as session:
        access = await access_for(update, session)
        if not access.is_owner:
            await query.edit_message_text("Solo l'owner può creare clienti.")
            return
        try:
            _customer, line = await create_customer_and_line(
                session,
                LineDraft(
                    name=state["name"],
                    service=service,
                    monthly_price_cents=state["price"],
                    months=state["months"],
                    reseller_id=state.get("reseller_id"),
                    notes="Creato dal bot: da creare nel pannello",
                ),
                today,
            )
        except ValueError as exc:
            await query.edit_message_text(f"Non creato: {exc}")
            return

        username = _username_for(state["name"], today)
        password = _password()
        expires = line.expires_on
        line_id = line.id
        label = ui.line_title(line)
        domain = await session.scalar(select(Setting).where(Setting.key == "vod_domain"))

    context.user_data.pop("new_line", None)

    panels = "LIVE e VOD (stesso username)" if service is Service.LIVE_VOD else "LIVE"
    body = [
        f"✅ <b>{ui.esc(label)}</b> creata (linea {line_id})",
        f"Scade {ui.italian_date(expires)}",
        "",
        f"<b>Da creare nel pannello {panels}</b>",
        f"username: <code>{username}</code>",
        f"password: <code>{password}</code>",
        f"durata: {state['months']} mesi",
    ]
    if service is Service.LIVE_VOD and domain is not None:
        body.extend(
            [
                "",
                "<b>Link VOD da consegnare</b>",
                f"<code>{ui.esc(domain.value)}/get.php?username={username}"
                f"&amp;password={password}</code>",
            ]
        )
    body.extend(["", "La linea resta «da creare nel pannello» finché il sync non la trova."])

    await query.edit_message_text(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Apri scheda", callback_data=f"card:{line_id}")],
                [InlineKeyboardButton("🏠 Menu", callback_data="menu:home")],
            ]
        ),
    )


def _username_for(name: str, today) -> str:
    """Stesso schema del pannello: nome senza spazi + giorno mese anno."""
    slug = "".join(ch for ch in normalize_key(name) if ch.isalnum())[:14]
    return f"{slug}{today:%d%m%y}"


def _password(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Passi del wizard che richiedono testo libero. True se consumato."""
    state = context.user_data.get("new_line")
    message = update.effective_message
    if not state or message is None or message.text is None:
        return False

    step = state.get("step")
    text = message.text.strip()

    if step == "name":
        if len(text) < 2:
            await message.reply_html("Scrivi nome e cognome del cliente.")
            return True
        state["name"] = text
        state["step"] = "service"
        await message.reply_html(
            f"🆕 <b>{ui.esc(text)}</b>\n\nQuale servizio?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Live", callback_data="new:service:live"),
                        InlineKeyboardButton("Live + VOD", callback_data="new:service:live_vod"),
                    ],
                    _cancel_row(),
                ]
            ),
        )
        return True

    if step == "price_text":
        cents = parse_price_to_cents(text)
        if cents is None:
            await message.reply_html("Prezzo non valido: scrivi per esempio <code>5</code>.")
            return True
        state["price"] = cents
        state["step"] = "months"
        body, markup = _ask_months(state)
        await message.reply_html(body, reply_markup=markup)
        return True

    if step == "months_text":
        if not text.isdigit() or not MIN_MONTHS <= int(text) <= MAX_MONTHS:
            await message.reply_html(f"Scrivi un numero di mesi tra {MIN_MONTHS} e {MAX_MONTHS}.")
            return True
        state["months"] = int(text)
        state["step"] = "confirm"
        body, markup = _summary(state)
        await message.reply_html(body, reply_markup=markup)
        return True

    return False
