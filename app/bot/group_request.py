"""Richiesta di rinnovo dal riepilogo del gruppo.

Tre passaggi, nessuno dei quali invia per sbaglio:
1. «Richiedi» sulla linea → si sceglie il periodo (`greq`);
2. periodo scelto → «Sei sicuro di voler richiedere …?» (`greqm`);
3. conferma → la richiesta parte e l'owner viene avvisato (`greqy`).

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
from app.services.months import MAX_MONTHS, MIN_MONTHS
from app.services.queries import get_line
from app.services.requests import RequestError, create_request

logger = logging.getLogger(__name__)


def _parse(data: str) -> tuple[int | None, int | None]:
    """ "greq:12" → (12, None); "greqm:12:3" → (12, 3)."""
    parts = data.split(":")
    line_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    months = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    return line_id, months


def _period(months: int) -> str:
    return "mese" if months == 1 else "mesi"


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
    """Primo tap: si sceglie il periodo. Non invia nulla."""
    query = update.callback_query
    chat = update.effective_chat
    if query is None or query.data is None or chat is None:
        return
    await query.answer()
    line_id, _months = _parse(query.data)
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

    presets = get_settings().duration_presets_list
    buttons = [
        InlineKeyboardButton(f"{months}", callback_data=f"greqm:{line_id}:{months}")
        for months in presets
    ]
    # Griglia 4x3: 1..12 in tre righe, così ogni mese ha il suo tasto.
    rows = [buttons[index : index + 4] for index in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton("✖️ Annulla", callback_data=f"greqn:{line_id}")])
    await editing.edit_text(
        "❓ <b>Quanti mesi vuoi richiedere?</b>\n\n"
        f"{ui.esc(title)} · scade {ui.italian_date(expires)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def pick_months(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodo scelto: si chiede conferma, senza inviare niente."""
    query = update.callback_query
    chat = update.effective_chat
    if query is None or query.data is None or chat is None:
        return
    await query.answer()
    line_id, months = _parse(query.data)
    if line_id is None or months is None:
        return
    editing = ui.Editor(query)

    if not MIN_MONTHS <= months <= MAX_MONTHS:
        await editing.edit_text(f"Il periodo deve stare tra {MIN_MONTHS} e {MAX_MONTHS} mesi.")
        return

    async with session_scope() as session:
        line = await _authorized_line(session, chat.id, line_id)
        if line is None:
            await editing.edit_text("Questa linea non è più disponibile in questo gruppo.")
            return
        if await _pending_for(session, line.id) is not None:
            await editing.edit_text(
                "⏳ C'è già una richiesta in attesa per questa linea.\nL'owner la sta valutando."
            )
            return
        title = ui.line_title(line)
        expires = line.expires_on

    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Sì, richiedi", callback_data=f"greqy:{line_id}:{months}")],
            [InlineKeyboardButton("✖️ No, annulla", callback_data=f"greqn:{line_id}")],
        ]
    )
    await editing.edit_text(
        "❓ <b>Sei sicuro di voler richiedere il rinnovo?</b>\n\n"
        f"{ui.esc(title)}\n"
        f"{months} {_period(months)} · scade {ui.italian_date(expires)}\n\n"
        "L'owner riceve la richiesta e decide.",
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Terzo tap: crea la richiesta e avvisa l'owner."""
    query = update.callback_query
    chat = update.effective_chat
    user = update.effective_user
    if query is None or query.data is None or chat is None:
        return
    await query.answer()
    line_id, months = _parse(query.data)
    if line_id is None or months is None:
        return
    editing = ui.Editor(query)

    if not MIN_MONTHS <= months <= MAX_MONTHS:
        await editing.edit_text(f"Il periodo deve stare tra {MIN_MONTHS} e {MAX_MONTHS} mesi.")
        return

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

    await editing.edit_text(
        "✅ <b>Richiesta inviata</b>\n\n"
        f"{ui.esc(title)}\n"
        f"{months} {_period(months)} · scade {ui.italian_date(expires)}\n"
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
    line_id, _months = _parse(query.data)
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
    try:
        await context.bot.send_message(
            chat_id=owner,
            text=(
                f"🔔 <b>Nuova richiesta dal gruppo</b>\n\n"
                f"{ui.esc(title)}\n{months} {_period(months)} · #{request_id}\n"
                f"da {ui.esc(who)} ({ui.esc(chat_title or 'gruppo')})\n\n"
                "Approva da qui o apri <code>/richieste</code>."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            f"✅ Approva {months} {_period(months)}",
                            callback_data=f"rqa:{request_id}:{months}",
                        )
                    ],
                    [InlineKeyboardButton("✖️ Rifiuta", callback_data=f"rqr:{request_id}")],
                ]
            ),
        )
    except Exception:
        logger.warning("Richiesta %s: avviso all'owner non riuscito", request_id)
