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
# Riepilogo del gruppo: invii distinti da quelli in privato, così un doppio
# avvio del job non manda due volte lo stesso messaggio.
KIND_GROUP_EXPIRING = "group_expiring"
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
    # Le linee marcate ma lontane dalla scadenza non entrano in `due`: senza
    # questi pulsanti resterebbero smarcabili solo a mano sul database.
    due_ids = {line_id for line_id, _title, _phrase in due[:8]}
    for line_id, title in flagged[:8]:
        if line_id in due_ids:
            continue
        short = title.split(" · ")[0]
        buttons.append(
            [
                InlineKeyboardButton(
                    f"↩️ Togli il segno · {short}", callback_data=f"nr:{line_id}:clear"
                )
            ]
        )
    for line_id, title, _phrase in due[:8]:
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Rinnova {title.split(' · ')[0].split()[0]}",
                    callback_data=f"renew:{line_id}:pick",
                ),
                InlineKeyboardButton(
                    "🚫 Non rinnova" if line_id not in flagged_ids else "↩️ Togli il segno",
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


async def build_group_reminder(
    today: date,
    *,
    reseller_id: int,
    chat_id: int,
    factory=None,
) -> tuple[str, InlineKeyboardMarkup | None] | None:
    """Scadenze dei prossimi giorni per il gruppo di un rivenditore.

    Solo le linee di quel rivenditore, con la finestra di `reminder_days`
    (oggi compreso). Nessun pulsante: in un gruppo i comandi dell'owner non
    hanno senso. Il messaggio parte ogni giorno, anche senza scadenze.
    """
    settings = get_settings()
    warn_days = settings.reminder_days
    session_factory = factory or get_session_factory()

    rows: list[tuple[int, int, str, date]] = []
    candidates = 0
    async with session_factory() as session:
        muted = await muted_line_ids(session, today)
        lines = list(
            (
                await session.scalars(
                    select(Line).where(
                        Line.deleted_at.is_(None),
                        Line.reseller_id == reseller_id,
                    )
                )
            ).all()
        )
        for line in lines:
            if line.id in muted:
                continue
            delta = days_left(line.expires_on, today)
            if delta < 0 or delta > warn_days:
                continue
            candidates += 1
            if not await _claim(
                session,
                kind=KIND_GROUP_EXPIRING,
                line_id=line.id,
                chat_id=chat_id,
                today=today,
            ):
                continue
            rows.append((line.id, delta, ui.line_title(line), line.expires_on))
        await session.commit()

    if not rows and candidates:
        # C'erano scadenze ma erano già state annunciate oggi: non si ripete,
        # e non si finge che non ci sia nulla.
        return None

    # Il messaggio parte ogni giorno, anche a lista vuota: è la conferma che
    # il gruppo è collegato e che il bot sta guardando le scadenze giuste.
    body = [
        f"⏰ <b>Scadenze</b> · prossimi {warn_days} giorni",
        "",
    ]
    if not rows:
        body.append("<i>Nessuna scadenza nei prossimi " + str(warn_days) + " giorni.</i>")
        return "\n".join(body), None

    for _line_id, delta, title, expires in sorted(rows, key=lambda row: row[1]):
        when = (
            "scade oggi" if delta == 0 else "scade domani" if delta == 1 else f"tra {delta} giorni"
        )
        body.append(f"• {ui.esc(title)} — {when} ({ui.italian_date(expires)})")
    body.extend(
        ["", "<i>Premi «Richiedi» sulla linea: scegli il periodo e ti chiedo conferma.</i>"]
    )
    buttons = [
        [
            InlineKeyboardButton(
                f"🔁 Richiedi · {_short_title(title)}",
                callback_data=f"greq:{line_id}",
            )
        ]
        for line_id, _delta, title, _expires in sorted(rows)
    ]
    return "\n".join(body), InlineKeyboardMarkup(buttons)


def _short_title(title: str, limit: int = 26) -> str:
    """Telegram vuole etichette corte: il nome per esteso sta nel testo."""
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"


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

    # Gruppi collegati: riepilogo autonomo, con le sole linee del rivenditore.
    for reseller_id, group_id in await _notifying_groups():
        try:
            group_payload = await build_group_reminder(
                today, reseller_id=reseller_id, chat_id=group_id
            )
        except Exception:
            logger.exception("Reminder: costruzione per il gruppo %s fallita", group_id)
            continue
        await _send(context, group_id, group_payload)

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


async def _notifying_groups() -> list[tuple[int, int]]:
    """Gruppi Telegram collegati a un rivenditore attivo con le notifiche on."""
    from app.db import get_session_factory
    from app.models import Reseller

    factory = get_session_factory()
    async with factory() as session:
        records = (
            await session.scalars(
                select(Reseller).where(
                    Reseller.active.is_(True),
                    Reseller.notify.is_(True),
                    Reseller.group_chat_id.is_not(None),
                )
            )
        ).all()
    return [(reseller.id, int(reseller.group_chat_id)) for reseller in records]


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
