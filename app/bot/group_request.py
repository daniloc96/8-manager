"""Richiesta di rinnovo dal riepilogo del gruppo.

Nel gruppo del rivenditore ogni linea in scadenza ha un pulsante «Richiedi».
Il primo tap non invia niente: chiede conferma. Solo il secondo tap crea la
richiesta, così un dito distratto non manda un rinnovo all'owner.

Chi preme non deve essere il rivenditore in persona: basta che il gruppo sia
quello collegato alla linea. La richiesta registra l'ID di chi ha premuto.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.config import get_settings
from app.db import session_scope
from app.models import RenewalRequest, RequestStatus, Reseller
from app.services.queries import get_line
from app.services.requests import RequestError, create_request

logger = logging.getLogger(__name__)


def default_months() -> int:
    """Periodo proposto dal bottone: il primo preset configurato (1 mese)."""
    presets = get_settings().duration_presets_list
    return presets[0] if presets else 1


def _line_id(data: str) -> int | None:
    _, _, raw = data.partition(":")
    return int(raw) if raw.isdigit() else None


async def _authorized_line(session, chat_id: int, line_id: int):
    """Linea valida solo se il gruppo è collegato al suo rivenditore."""
    reseller = await session.scalar(
        select(Reseller).where(
            Reseller.group_chat_id == chat_id,
            Reseller.active.is_(True),
        )
    )
    if reseller is None:
        return None
    line = await get_line(session, line_id)
    if line is None or line.reseller_id != reseller.id:
        return None
    return line


async def _pending_for(session, line_id: int) -> RenewalRequest | None:
    return await session.scalar(
        select(RenewalRequest).where(
            RenewalRequest.line_id == line_id,
            RenewalRequest.status == RequestStatus.PENDING,
        )
    )


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Primo tap: mostra la domanda di conferma, non invia nulla."""
    query = update.callback_query
    chat = update.effective_chat
    if query is None or query.data is None or chat is None:
        return
    await query.answer()
    line_id = _line_id(query.data)
    if line_id is None:
        return
    editing = ui.Editor(query)

    async with session_scope() as session:
        line = await _authorized_line(session, chat.id, line_id)
        if line is None:
            await editing.edit_text(
                "Questa linea non è più disponibile: il riepilogo è cambiato, "
                "aspetta il messaggio di domani."
            )
            return
        waiting = await _pending_for(session, line.id)
        title = ui.line_title(line)
        expires = line.expires_on

    if waiting is not None:
        await editing.edit_text(
            f"⏳ C'è già una richiesta in attesa per <b>{ui.esc(title)}</b>.\n"
            "L'owner la sta valutando.",
            parse_mode=ParseMode.HTML,
        )
        return

    months = default_months()
    period = "mese" if months == 1 else "mesi"
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Sì, richiedi", callback_data=f"greqy:{line_id}")],
            [InlineKeyboardButton("✖️ No, annulla", callback_data=f"greqn:{line_id}")],
        ]
    )
    await editing.edit_text(
        "❓ <b>Sei sicuro di voler richiedere il rinnovo?</b>\n\n"
        f"{ui.esc(title)}\n"
        f"{months} {period} · scade {ui.italian_date(expires)}\n\n"
        "L'owner riceve la richiesta e decide.",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Secondo tap: crea la richiesta e avvisa l'owner."""
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    if query is None or query.data is None or chat is None:
        return
    await query.answer()
    line_id = _line_id(query.data)
    if line_id is None:
        return
    editing = ui.Editor(query)

    async with session_scope() as session:
        line = await _authorized_line(session, chat.id, line_id)
        if line is None:
            await editing.edit_text("Questa linea non è più disponibile in questo gruppo.")
            return
        waiting = await _pending_for(session, line.id)
        if waiting is not None:
            await editing.edit_text(
                "⏳ La richiesta per questa linea era già stata inviata.\nL'owner la sta valutando."
            )
            return

        months = default_months()
        try:
            request = await create_request(
                session,
                line_id=line.id,
                requested_by=user.id if user else 0,
                months=months,
                note=f"richiesta dal gruppo · {chat.title or chat.id}",
                origin_chat_id=chat.id,
                origin_message_id=query.message.message_id if query.message else None,
            )
        except RequestError as exc:
            await editing.edit_text(f"Richiesta non inviata: {exc}")
            return
        request_id = request.id
        title = ui.line_title(line)
        expires = line.expires_on

    period = "mese" if months == 1 else "mesi"
    await editing.edit_text(
        "✅ <b>Richiesta inviata</b>\n\n"
        f"{ui.esc(title)}\n"
        f"{months} {period} · scade {ui.italian_date(expires)}\n"
        "Ti avviso appena l'owner risponde.",
        parse_mode=ParseMode.HTML,
    )
    await _notify_owner(
        context,
        request_id=request_id,
        title=title,
        months=months,
        who=_who(user),
        chat_title=chat.title,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    line_id = _line_id(query.data)
    if line_id is None:
        return
    await ui.Editor(query).edit_text(
        "✖️ Richiesta annullata: non ho inviato niente.\n"
        "Se serve, premi di nuovo «Richiedi» sulla linea."
    )


def _who(user) -> str:
    if user is None:
        return "qualcuno nel gruppo"
    name = user.full_name or (user.username and f"@{user.username}") or str(user.id)
    if user.username and not user.username.startswith("@"):
        name = f"{name} (@{user.username})"
    return name


async def _notify_owner(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    request_id: int,
    title: str,
    months: int,
    who: str,
    chat_title: str | None,
) -> None:
    """L'owner deve saperlo subito: le richieste non si scoprono da sole."""
    owner = get_settings().owner_telegram_id
    if owner is None or context.bot is None:
        return
    period = "mese" if months == 1 else "mesi"
    try:
        await context.bot.send_message(
            chat_id=owner,
            text=(
                f"🔔 <b>Nuova richiesta dal gruppo</b>\n\n"
                f"{ui.esc(title)}\n{months} {period} · #{request_id}\n"
                f"da {ui.esc(who)} ({ui.esc(chat_title or 'gruppo')})\n\n"
                "Rispondi con <code>/richieste</code>."
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        logger.warning("Richiesta %s: avviso all'owner non riuscito", request_id)
