from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.config import get_settings
from app.db import get_session_factory
from app.models import Line, Notification, ReviewException
from app.services.months import days_left
from app.services.queries import muted_line_ids
from app.services.review_exceptions import is_due

logger = logging.getLogger(__name__)

KIND_EXPIRING = "expiring"
KIND_EXPIRED = "expired"
KIND_EXCEPTION = "exception"


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def _claim(session, *, kind: str, line_id: int, chat_id: int, today: date) -> bool:
    """True se questo invito non è ancora stato fatto oggi."""
    existing = await session.scalar(
        select(Notification.id).where(
            Notification.kind == kind,
            Notification.line_id == line_id,
            Notification.chat_id == chat_id,
            Notification.sent_on == today,
        )
    )
    if existing is not None:
        return False
    session.add(Notification(kind=kind, line_id=line_id, chat_id=chat_id, sent_on=today))
    await session.flush()
    return True


async def build_reminder(
    today: date,
    *,
    reseller_id: int | None = None,
    chat_id: int,
    include_exceptions: bool = True,
    factory=None,
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Riepilogo scadenze per una chat: owner (tutte) o rivenditore (le sue)."""
    settings = get_settings()
    warn_days = settings.reminder_days
    session_factory = factory or get_session_factory()

    async with session_factory() as session:
        muted = await muted_line_ids(session, today)
        due: list[tuple[int, str, str]] = []
        flagged: list[tuple[int, str]] = []
        flagged_ids: set[int] = set()
        lines = list((await session.scalars(select(Line).where(Line.deleted_at.is_(None)))).all())
        for line in lines:
            if line.id in muted:
                continue
            if reseller_id is not None and line.reseller_id != reseller_id:
                continue
            # La marcatura si vede sempre, anche se la linea è lontana dalla
            # scadenza: serve come promemoria di non rinnovare.
            if line.no_renewal:
                flagged_ids.add(line.id)
                flagged.append((line.id, ui.line_title(line)))
            delta = days_left(line.expires_on, today)
            if delta < -1 or delta > warn_days:
                continue
            kind = KIND_EXPIRED if delta < 0 else KIND_EXPIRING
            if not await _claim(session, kind=kind, line_id=line.id, chat_id=chat_id, today=today):
                continue
            due.append((line.id, ui.line_title(line), ui.expiry_phrase(line, today)))

        exceptions = []
        for record in (
            (await session.scalars(select(ReviewException).order_by(ReviewException.line_id))).all()
            if include_exceptions
            else []
        ):
            if not is_due(record, today):
                continue
            if not await _claim(
                session,
                kind=KIND_EXCEPTION,
                line_id=record.line_id,
                chat_id=chat_id,
                today=today,
            ):
                continue
            exceptions.append(record)

        await session.commit()

    if not due and not exceptions and not flagged:
        return None

    body = [f"⏰ <b>Scadenze</b> · {ui.italian_date(today)}", ""]
    body.extend(f"• {ui.esc(title)} — {ui.esc(phrase)}" for _id, title, phrase in due)
    if flagged:
        body.append("")
        body.append("<b>Da non rinnovare</b>")
        body.extend(f"• {ui.esc(title)}" for _id, title in flagged)
    if exceptions:
        body.append("")
        body.append("<b>Eccezioni da rivedere</b>")
        body.extend(f"• linea {record.line_id} — {ui.esc(record.reason)}" for record in exceptions)

    buttons = []
    for line_id, title, _phrase in due[:8]:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Rinnova {title.split(' · ')[0].split()[0]}",
                    callback_data=f"renew:{line_id}:pick",
                ),
                InlineKeyboardButton(
                    "🚫 Non rinnova" if line_id not in flagged_ids else "↩️ Rinnova lo stesso",
                    callback_data=(
                        f"nr:{line_id}:set" if line_id not in flagged_ids else f"nr:{line_id}:clear"
                    ),
                ),
            ]
        )
    buttons.append([InlineKeyboardButton("📅 Tutte le scadenze", callback_data="menu:due")])
    if not due and flagged and not exceptions:
        # Solo linee già marcate: resta utile vedere il promemoria.
        body.append("")
        body.append("<i>Nessun'altra scadenza da rinnovare.</i>")
    return "\n".join(body), InlineKeyboardMarkup(buttons)


async def build_owner_reminder(
    today: date, *, factory=None
) -> tuple[str, InlineKeyboardMarkup] | None:
    """Scorciatoia usata dai test: riepilogo completo per l'owner."""
    return await build_reminder(
        today,
        chat_id=get_settings().owner_telegram_id or 0,
        factory=factory,
    )


async def _send(context, chat_id: int, payload) -> None:
    if payload is None:
        return
    body, markup = payload
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=body,
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
    except Exception:
        logger.exception("Reminder: invio a %s fallito", chat_id)


async def reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if settings.owner_telegram_id is None:
        return

    today = _today()

    try:
        owner_payload = await build_owner_reminder(today)
    except Exception:
        logger.exception("Reminder: costruzione del riepilogo owner fallita")
        owner_payload = None

    if owner_payload is None:
        logger.info("Reminder: nessuna scadenza per l'owner")
    else:
        await _send(context, settings.owner_telegram_id, owner_payload)

    resellers = await _notifying_resellers()
    for reseller_id, chat_id in resellers:
        try:
            payload = await build_reminder(
                today,
                reseller_id=reseller_id,
                chat_id=chat_id,
                include_exceptions=False,
            )
        except Exception:
            logger.exception("Reminder: costruzione per il rivenditore %s fallita", chat_id)
            continue
        await _send(context, chat_id, payload)


async def _notifying_resellers() -> list[tuple[int, int]]:
    from app.db import get_session_factory
    from app.models import Reseller

    factory = get_session_factory()
    async with factory() as session:
        records = (
            await session.scalars(
                select(Reseller).where(
                    Reseller.active.is_(True),
                    Reseller.notify.is_(True),
                    Reseller.telegram_user_id.is_not(None),
                )
            )
        ).all()
        return [
            (record.id, record.telegram_user_id)
            for record in records
            if record.telegram_user_id is not None
        ]
