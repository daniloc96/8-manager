from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Line, ReviewException


class ReviewExceptionError(ValueError):
    """Eccezione non applicabile alla linea."""


async def load_exceptions(session: AsyncSession) -> dict[int, ReviewException]:
    """Tutte le eccezioni, indicizzate per linea."""
    records = (await session.scalars(select(ReviewException))).all()
    return {record.line_id: record for record in records}


def is_active(record: ReviewException, today: date) -> bool:
    """L'eccezione nasconde la review finché la data di revisione è nel futuro."""
    return record.review_on is None or record.review_on > today


def is_due(record: ReviewException, today: date) -> bool:
    return record.review_on is not None and record.review_on <= today


async def set_exception(
    session: AsyncSession,
    *,
    line_id: int,
    reason: str,
    review_on: date | None = None,
) -> ReviewException:
    line = await session.get(Line, line_id)
    if line is None:
        raise ReviewExceptionError(f"linea {line_id} non trovata")
    if line.deleted_at is not None:
        raise ReviewExceptionError(f"linea {line_id} disattivata")

    record = await session.scalar(select(ReviewException).where(ReviewException.line_id == line_id))
    cleaned = reason.strip()
    if not cleaned:
        raise ReviewExceptionError("serve un motivo per l'eccezione")

    if record is None:
        record = ReviewException(line_id=line_id, reason=cleaned, review_on=review_on)
        session.add(record)
    else:
        record.reason = cleaned
        record.review_on = review_on

    await session.flush()
    return record


async def clear_exception(session: AsyncSession, *, line_id: int) -> ReviewException:
    record = await session.scalar(select(ReviewException).where(ReviewException.line_id == line_id))
    if record is None:
        raise ReviewExceptionError(f"nessuna eccezione per la linea {line_id}")
    await session.delete(record)
    await session.flush()
    return record
