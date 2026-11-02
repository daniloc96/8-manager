from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Line
from app.models.base import utcnow


class NoRenewalError(ValueError):
    """Non si può marcare la linea."""


async def set_no_renewal(
    session: AsyncSession, line_id: int, flagged: bool, *, actor_telegram_id: int | None = None
) -> tuple[Line, bool]:
    """Segna la linea come «non rinnovare» (o toglie il segno).

    Non tocca la scadenza né il pannello: è una nota operativa per l'owner,
    che ricorda di non rinnovare quando la linea scade. La linea esce dal bot
    solo quando sparisce dai pannelli.
    """
    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise NoRenewalError("linea non trovata o disattivata.")

    if bool(line.no_renewal) == flagged:
        return line, False

    previous = line.no_renewal
    line.no_renewal = flagged
    line.no_renewal_at = utcnow() if flagged else None
    session.add(
        AuditLog(
            action="line_no_renewal_set" if flagged else "line_no_renewal_clear",
            entity="line",
            entity_id=line.id,
            payload={
                "old": previous,
                "new": flagged,
                "actor_telegram_id": actor_telegram_id,
                "at": datetime.now().isoformat(timespec="seconds"),
            },
        )
    )
    await session.flush()
    return line, True


def is_flagged(line: Line) -> bool:
    return bool(getattr(line, "no_renewal", False))
