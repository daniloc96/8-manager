from __future__ import annotations

import html
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import texts, ui
from app.bot.auth import access_for, is_group
from app.config import get_settings
from app.db import session_scope
from app.models import Customer, Line, Reseller, Setting
from app.services import settings as settings_service
from app.services.queries import due_groups, income_report, lines_of_reseller
from app.services.text import format_eur

logger = logging.getLogger(__name__)

# Un utente sconosciuto che preme /start non deve tempestare l'owner.
_notified: dict[int, float] = {}


def _today():
    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def _target(update: Update):
    """Dove scrivere: modifica il messaggio del bottone o risponde al testo.

    È `async` perché i chiamanti fanno `await _target(update)`: restituisce il
    metodo (già legato) da invocare, e attenderlo qui evita di await-are un
    metodo sincrono, che solleva TypeError.
    """
    query = update.callback_query
    if query is not None:
        return query.edit_message_text
    message = update.effective_message
    if message is None:
        return None
    return ui.html_target(message)


async def _access_of(update: Update):
    from app.db import session_scope

    user = update.effective_user
    if user is None:
        return None, None
    async with session_scope() as session:
        return user, await access_for(update, session)


async def _require_owner(update: Update) -> bool:
    _user, access = await _access_of(update)
    return bool(access and access.is_owner)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    chat = update.effective_chat
    if chat is not None and is_group(chat.type):
        # nei gruppi il menu non serve: si usano i comandi indirizzati
        return

    async with session_scope() as session:
        access = await access_for(update, session)

    if not access.allowed:
        await message.reply_html(
            texts.START_GUEST.format(
                name=html.escape(user.full_name or "amico"),
                user_id=user.id,
            )
        )
        await _notify_owner_unknown(context, user)
        return

    today = _today()
    async with session_scope() as session:
        groups = await due_groups(
            session,
            today,
            warn_days=get_settings().reminder_days,
            reseller_id=None if access.is_owner else access.reseller_id,
        )

    greeting = texts.MENU if access.is_owner else texts.RESELLER_MENU.format(name=access.name)
    await message.reply_html(
        f"{greeting}\n{texts.due_summary(groups.total)}",
        reply_markup=ui.menu_keyboard(is_owner=access.is_owner, is_reseller=access.is_reseller),
    )


async def _notify_owner_unknown(context: ContextTypes.DEFAULT_TYPE, user) -> None:
    """Chiede all'owner se autorizzare chi ha scritto senza permessi."""
    owner_id = get_settings().owner_telegram_id
    if owner_id is None or context.bot is None or user is None:
        return
    now = time.monotonic()
    last = _notified.get(user.id, 0.0)
    if now - last < 3600:
        return
    _notified[user.id] = now
    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                "🔐 <b>Richiesta di accesso</b>\n\n"
                f"{ui.esc(user.full_name or 'Sconosciuto')} "
                f"(@{ui.esc(user.username or 'senza username')}, ID <code>{user.id}</code>)"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Autorizza come rivenditore", callback_data=f"auth:{user.id}:yes"
                        ),
                        InlineKeyboardButton("Ignora", callback_data=f"auth:{user.id}:no"),
                    ]
                ]
            ),
        )
    except Exception:
        logger.exception("Accesso: notifica all'owner fallita")


async def menu_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    context.user_data.pop("awaiting", None)
    target = await _target(update)
    if target is None:
        return
    _user, access = await _access_of(update)
    if access is None or not access.allowed:
        await target("Non sei autorizzato.")
        return
    body = texts.MENU if access.is_owner else texts.RESELLER_MENU.format(name=access.name)
    await target(
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=ui.menu_keyboard(is_owner=access.is_owner, is_reseller=access.is_reseller),
    )


async def show_due(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scadenze: scadute, oggi e entro i giorni di preavviso."""
    query = update.callback_query
    if query is not None:
        await query.answer()
    user, access = await _access_of(update)
    target = await _target(update)
    if target is None:
        return
    if access is None or not access.allowed:
        await target(texts.UNAUTHORIZED.format(user_id=user.id if user else "?"))
        return

    today = _today()
    async with session_scope() as session:
        groups = await due_groups(
            session,
            today,
            warn_days=get_settings().reminder_days,
            reseller_id=None if access.is_owner else access.reseller_id,
        )
        sections = (
            ("scadute", groups.expired),
            ("oggi", groups.today),
            ("in scadenza", groups.soon),
        )
        body = [f"📅 <b>Scadenze</b> · {ui.italian_date(today)}"]
        total = 0
        for title, group in sections:
            if not group:
                continue
            total += len(group)
            body.append("")
            body.append(f"<b>{title}</b> ({len(group)})")
            body.extend(ui.line_row(line, today) for line in group[:15])
            if len(group) > 15:
                body.append(f"… e altre {len(group) - 15}")
        if total == 0:
            body.append("")
            body.append("✅ Nessuna linea scaduta o in scadenza.")

        if access.is_owner:
            buttons = [
                [
                    InlineKeyboardButton(
                        f"Rinnova {line.customer.name.split()[0]}",
                        callback_data=f"renew:{line.id}:pick",
                    ),
                    InlineKeyboardButton("Apri", callback_data=f"card:{line.id}"),
                ]
                for line in (groups.today + groups.soon + groups.expired)[:10]
            ]
        else:
            buttons = [
                [
                    InlineKeyboardButton(
                        f"Richiedi · {line.customer.name.split()[0]}",
                        callback_data=f"rq:{line.id}",
                    )
                ]
                for line in (groups.today + groups.soon + groups.expired)[:10]
            ]
    buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])

    await target(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def show_income(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
    if not await _require_owner(update):
        target = await _target(update)
        user = update.effective_user
        if target is not None:
            await target(texts.UNAUTHORIZED.format(user_id=user.id if user else "?"))
        return

    target = await _target(update)
    if target is None:
        return

    today = _today()
    async with session_scope() as session:
        report = await income_report(session, today.year, today.month)

    body = [f"💰 <b>Incassi</b> · {today.month:02d}/{today.year}"]
    if not report.rows:
        body.append("")
        body.append("Nessun rinnovo registrato questo mese.")
    else:
        body.append("")
        for row in report.rows:
            who = row.reseller or "Diretto"
            body.append(f"• {ui.esc(who)} — {row.renewals} rinnovi · {format_eur(row.total_cents)}")
        body.append("")
        body.append(f"<b>Totale: {format_eur(report.total_cents)}</b>")

    body.append("")
    body.append(
        f"📦 Parco: <b>{report.active_lines} linee attive</b> · "
        f"{format_eur(report.monthly_cents)}/mese"
    )

    await target(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]
        ),
    )


async def show_resellers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
    if not await _require_owner(update):
        target = await _target(update)
        user = update.effective_user
        if target is not None:
            await target(texts.UNAUTHORIZED.format(user_id=user.id if user else "?"))
        return

    target = await _target(update)
    if target is None:
        return

    async with session_scope() as session:
        resellers = list((await session.scalars(select(Reseller).order_by(Reseller.name))).all())
        rows: list[str] = []
        for reseller in resellers:
            lines = await lines_of_reseller(session, reseller.id)
            if not lines:
                continue
            live = sum(1 for line in lines if line.service.value == "live")
            monthly = sum(line.monthly_price_cents for line in lines)
            access = (
                f"ID <code>{reseller.telegram_user_id}</code>"
                if reseller.telegram_user_id
                else "Telegram non collegato"
            )
            rows.append(
                f"• <b>{ui.esc(reseller.name)}</b> — {len(lines)} linee "
                f"({live} live) · {format_eur(monthly)}/mese · {access}"
            )

    body = ["👥 <b>Rivenditori</b>", ""]
    body.extend(rows or ["Nessun rivenditore con linee attive."])
    if not rows:
        body.append("")
        body.append(
            "Le linee importate dai pannelli sono tutte «Diretto»: il registro "
            "rivenditori si popola in fase C."
        )

    await target(
        "\n".join(body),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]
        ),
    )


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()
    if not await _require_owner(update):
        target = await _target(update)
        user = update.effective_user
        if target is not None:
            await target(texts.UNAUTHORIZED.format(user_id=user.id if user else "?"))
        return

    target = await _target(update)
    if target is None:
        return

    async with session_scope() as session:
        values = list((await session.scalars(select(Setting).order_by(Setting.key))).all())
        editable_values = await settings_service.all_values(session)
        customers = await session.scalar(select(func.count(Customer.id))) or 0
        lines = (
            await session.scalar(select(func.count(Line.id)).where(Line.deleted_at.is_(None))) or 0
        )

    body = [
        "⚙️ <b>Impostazioni</b>",
        "",
        f"Clienti: <b>{customers}</b> · Linee attive: <b>{lines}</b>",
        "",
        "<b>Modificabili dal bot</b>",
    ]
    for key, definition in settings_service.EDITABLE_SETTINGS.items():
        value = editable_values.get(key)
        shown = f"<code>{ui.esc(value)}</code>" if value else "<i>non impostato</i>"
        body.append(f"• <b>{ui.esc(definition.label)}</b> — {shown}")
    body.append("")
    body.append("<b>Altri valori salvati</b>")
    extra = [item for item in values if item.key not in settings_service.EDITABLE_SETTINGS]
    if extra:
        body.extend(f"• <code>{ui.esc(item.key)}</code>: {ui.esc(item.value)}" for item in extra)
    else:
        body.append("Nessuno: si usano i default di <code>.env</code>.")

    rows = [
        [InlineKeyboardButton(f"✏️ {definition.label}", callback_data=f"set:{key}")]
        for key, definition in settings_service.EDITABLE_SETTINGS.items()
    ]
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(rows)

    await target("\n".join(body), parse_mode=ParseMode.HTML, reply_markup=markup)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Instradamento dei bottoni del menu."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    data = query.data

    if data == "menu:search":
        await query.answer()
        context.user_data["awaiting"] = "search"
        await query.edit_message_text(
            texts.SEARCH_PROMPT,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🏠 Menu", callback_data="menu:home")]]
            ),
        )
        return

    if data == "menu:new":
        from app.bot.newline import start as start_new_line

        await start_new_line(update, context)
        return

    if data == "menu:request":
        from app.bot.reseller import ask_which_line

        # «Indietro» dal picker mesi: l'attesa di un numero decade.
        context.user_data.pop("awaiting", None)
        await ask_which_line(update, context)
        return

    handlers = {
        "menu:home": menu_home,
        "menu:due": show_due,
        "menu:income": show_income,
        "menu:resellers": show_resellers,
        "menu:settings": show_settings,
    }
    handler = handlers.get(data)
    if handler is not None:
        await handler(update, context)
