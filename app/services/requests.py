from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Line,
    RenewalRequest,
    RenewalSource,
    RequestStatus,
    Reseller,
    utcnow,
)
from app.services.lines import renew_line
from app.services.months import MAX_MONTHS, MIN_MONTHS


class RequestError(ValueError):
    """Richiesta di rinnovo non valida."""


async def create_request(
    session: AsyncSession,
    *,
    line_id: int,
    requested_by: int,
    months: int,
    note: str | None = None,
    origin_chat_id: int | None = None,
    origin_message_id: int | None = None,
) -> RenewalRequest:
    if not MIN_MONTHS <= months <= MAX_MONTHS:
        raise RequestError(f"i mesi devono stare tra {MIN_MONTHS} e {MAX_MONTHS}")

    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise RequestError("linea non trovata o disattivata")

    request = RenewalRequest(
        line_id=line.id,
        requested_by_telegram_id=requested_by,
        months=months,
        note=note,
        status=RequestStatus.PENDING,
        origin_chat_id=origin_chat_id,
        origin_message_id=origin_message_id,
    )
    session.add(request)
    await session.flush()
    return request


async def pending_requests(session: AsyncSession) -> list[RenewalRequest]:
    return list(
        (
            await session.scalars(
                select(RenewalRequest)
                .where(RenewalRequest.status == RequestStatus.PENDING)
                .order_by(RenewalRequest.id)
            )
        ).all()
    )


async def approve_request(
    session: AsyncSession,
    *,
    request_id: int,
    months: int,
    today: date,
    approved_by: int | None,
) -> tuple[RenewalRequest, Line]:
    request = await session.get(RenewalRequest, request_id)
    if request is None or request.status is not RequestStatus.PENDING:
        raise RequestError("richiesta non trovata o già gestita")
    if not MIN_MONTHS <= months <= MAX_MONTHS:
        raise RequestError(f"i mesi devono stare tra {MIN_MONTHS} e {MAX_MONTHS}")

    line = await session.get(Line, request.line_id)
    if line is None or line.deleted_at is not None:
        raise RequestError("linea non trovata o disattivata")

    renewal = await renew_line(
        session,
        line,
        months=months,
        today=today,
        source=RenewalSource.REQUEST,
        actor_telegram_id=approved_by,
        note=request.note,
    )
    request.status = RequestStatus.APPROVED
    request.months = months
    request.renewal_id = renewal.id
    request.resolved_at = utcnow()
    request.resolved_by_telegram_id = approved_by
    await session.flush()
    return request, line


async def reject_request(
    session: AsyncSession, *, request_id: int, rejected_by: int | None
) -> RenewalRequest:
    request = await session.get(RenewalRequest, request_id)
    if request is None or request.status is not RequestStatus.PENDING:
        raise RequestError("richiesta non trovata o già gestita")
    request.status = RequestStatus.REJECTED
    request.resolved_at = utcnow()
    request.resolved_by_telegram_id = rejected_by
    await session.flush()
    return request


async def link_group(
    session: AsyncSession,
    *,
    reseller_id: int,
    chat_id: int,
    title: str | None = None,
) -> Reseller:
    """Collega un gruppo Telegram a un rivenditore (uno per gruppo)."""
    reseller = await session.get(Reseller, reseller_id)
    if reseller is None:
        raise RequestError(f"rivenditore {reseller_id} non trovato")

    previous = await session.scalar(
        select(Reseller).where(Reseller.group_chat_id == chat_id, Reseller.id != reseller.id)
    )
    if previous is not None:
        previous.group_chat_id = None
        previous.group_title = None

    reseller.group_chat_id = chat_id
    reseller.group_title = title
    await session.flush()
    return reseller


async def unlink_group(session: AsyncSession, *, chat_id: int) -> Reseller | None:
    reseller = await session.scalar(select(Reseller).where(Reseller.group_chat_id == chat_id))
    if reseller is None:
        return None
    reseller.group_chat_id = None
    reseller.group_title = None
    await session.flush()
    return reseller


async def reseller_by_name(session: AsyncSession, name: str) -> Reseller | None:
    from app.services.text import normalize_key

    return await session.scalar(select(Reseller).where(Reseller.name_key == normalize_key(name)))


async def authorize_reseller(
    session: AsyncSession, *, telegram_user_id: int, name: str
) -> Reseller:
    """Collega (o crea) il rivenditore per un Telegram ID."""
    from app.services.text import normalize_key

    existing = await session.scalar(
        select(Reseller).where(Reseller.telegram_user_id == telegram_user_id)
    )
    if existing is not None:
        existing.active = True
        return existing

    cleaned = name.strip() or f"Rivenditore {telegram_user_id}"
    key = normalize_key(cleaned)
    record = await session.scalar(select(Reseller).where(Reseller.name_key == key))
    if record is not None:
        record.telegram_user_id = telegram_user_id
        record.active = True
        return record

    record = Reseller(
        name=cleaned,
        name_key=key,
        telegram_user_id=telegram_user_id,
        notify=True,
        active=True,
    )
    session.add(record)
    await session.flush()
    return record
