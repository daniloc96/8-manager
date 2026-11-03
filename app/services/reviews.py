"""Cosa il bot ha segnalato l'ultima volta che ha sincronizzato i pannelli.

Serve all'owner per tornare su una singola anomalia dopo aver letto il riepilogo
del `/sync`: le righe da rivedere vengono salvate con la linea a cui si
riferiscono, così la scheda del cliente può mostrare «cosa c'è da sistemare» e
offrire l'azione giusta.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Line, ReviewException
from app.panels.sync import ReviewItem, SyncIssue, SyncReport
from app.services.review_exceptions import ReviewExceptionError, set_exception

logger = logging.getLogger(__name__)


async def store_reviews(session: AsyncSession, report: SyncReport) -> int:
    """Sostituisce le review salvate con quelle dell'ultimo giro.

    Le review senza `line_id` (righe non ancora collegate a una linea, per
    esempio username doppi nel pannello) restano solo nel messaggio del sync:
    non c'è una linea su cui agire nel bot.
    """
    items = [
        review
        for result in report.results
        for review in result.review
        if review.line_id is not None
    ]
    preceding = await latest_run_at(session)

    await session.execute(delete(ReviewException).where(ReviewException.issue.is_not(None)))
    for review in items:
        line_id = review.line_id
        if line_id is None:  # pragma: no cover - filtrato sopra
            continue
        line = await session.get(Line, line_id)
        if line is None or line.deleted_at is not None:
            continue
        existing = await session.scalar(
            select(ReviewException).where(ReviewException.line_id == line_id)
        )
        if existing is not None:
            # eccezione già data dall'owner: non la sovrascriviamo col sync
            continue
        session.add(
            ReviewException(
                line_id=line_id,
                reason=review.reason[:200],
                review_on=None,
                issue=review.issue,
                local_expires=review.local_expires,
                panel_expires=review.panel_expires,
            )
        )
    await session.flush()
    logger.info("Sync: salvate %s anomalie da rivedere, giro delle %s", len(items), preceding)
    return len(items)


async def latest_run_at(session: AsyncSession) -> datetime | None:
    record = await session.scalar(
        select(ReviewException.created_at)
        .where(ReviewException.issue.is_not(None))
        .order_by(ReviewException.created_at.desc())
    )
    return record


async def open_issue(session: AsyncSession, *, line_id: int) -> ReviewException | None:
    """L'anomalia pendente su una linea, se c'è."""
    return await session.scalar(select(ReviewException).where(ReviewException.line_id == line_id))


async def open_issues(session: AsyncSession) -> list[tuple[ReviewException, Line]]:
    """Solo le anomalie segnalate dal sync.

    Le eccezioni già accettate dall'owner non sono «da sistemare»: restano in
    tabella per zittire la review, ma qui non si elencano.
    """
    rows = await session.execute(
        select(ReviewException, Line)
        .join(Line, Line.id == ReviewException.line_id)
        .where(ReviewException.issue.is_not(None))
        .order_by(Line.id)
    )
    return [(record, line) for record, line in rows.all()]


def is_solvable(record: ReviewException) -> bool:
    """Solo le anomalie per cui il bot sa fare qualcosa."""
    return record.issue == SyncIssue.LOCAL_AHEAD.value


def _today() -> date:
    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def align_to_panel(
    session: AsyncSession,
    *,
    line_id: int,
    actor_telegram_id: int | None,
) -> tuple[Line, date, date]:
    """Porta la data locale a quella del pannello, che è la fonte di verità."""
    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise ReviewExceptionError("linea non trovata o disattivata")
    record = await open_issue(session, line_id=line_id)
    if record is None or record.panel_expires is None:
        raise ReviewExceptionError("questa linea non ha una data del pannello da copiare")

    previous = line.expires_on
    target = record.panel_expires
    line.expires_on = target

    from app.models import AuditLog

    session.add(
        AuditLog(
            action="review_align",
            entity="line",
            entity_id=line.id,
            actor_telegram_id=actor_telegram_id,
            payload={
                "old": previous.isoformat(),
                "new": target.isoformat(),
                "issue": record.issue,
            },
        )
    )
    await session.delete(record)
    await session.flush()
    return line, previous, target


async def accept_local(
    session: AsyncSession,
    *,
    line_id: int,
    actor_telegram_id: int | None,
) -> tuple[Line, date]:
    """Tiene la data locale e accetta lo scarto.

    Non è «ignora per sempre»: il locale è più avanti del pannello perché il
    rinnovo non è ancora stato fatto sul pannello. L'eccezione scade quando il
    locale scade, così se a quel punto il pannello non è stato rinnovato
    l'anomalia torna a farsi vedere.
    """
    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise ReviewExceptionError("linea non trovata o disattivata")
    record = await open_issue(session, line_id=line_id)
    if record is None:
        raise ReviewExceptionError("nessuna anomalia da accettare su questa linea")

    local = record.local_expires or line.expires_on
    await set_exception(
        session,
        line_id=line_id,
        reason=f"data locale più avanti del pannello: tengo {local:%d/%m/%Y}",
        review_on=local,
    )
    stored = await open_issue(session, line_id=line_id)
    if stored is not None:
        stored.issue = SyncIssue.EXPIRY_DIFF.value
        stored.local_expires = local
        stored.panel_expires = record.panel_expires
    from app.models import AuditLog

    session.add(
        AuditLog(
            action="review_accept",
            entity="line",
            entity_id=line.id,
            actor_telegram_id=actor_telegram_id,
            payload={"local": local.isoformat(), "review_on": local.isoformat()},
        )
    )
    await session.flush()
    return line, local


def describe(record: ReviewException) -> str:
    """Testo breve dell'anomalia, con le due date quando servono."""
    if record.issue == SyncIssue.LOCAL_AHEAD.value and record.local_expires is not None:
        panel = (
            f"{record.panel_expires:%d/%m/%Y}"
            if record.panel_expires is not None
            else "sconosciuta"
        )
        return f"locale {record.local_expires:%d/%m/%Y} · pannello {panel}"
    return record.reason


def dump(record: ReviewException) -> str:  # pragma: no cover - debug
    return json.dumps(
        {
            "line_id": record.line_id,
            "issue": record.issue,
            "reason": record.reason,
            "local_expires": record.local_expires,
            "panel_expires": record.panel_expires,
        },
        default=str,
    )


__all__ = [
    "ReviewItem",
    "accept_local",
    "align_to_panel",
    "describe",
    "dump",
    "is_solvable",
    "latest_run_at",
    "open_issue",
    "open_issues",
    "store_reviews",
]
