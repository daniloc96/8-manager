from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Customer,
    Line,
    NewLineRequest,
    PanelLine,
    RequestStatus,
    Reseller,
    Service,
    utcnow,
)
from app.services.anagrafica import NOTES_MAX
from app.services.credentials import ensure_username, password, username_for
from app.services.lines import LineDraft, create_customer_and_line
from app.services.months import MAX_MONTHS, MIN_MONTHS

NAME_MAX = 160


class NewLineRequestError(ValueError):
    """Richiesta di nuova linea non valida o già gestita."""


def _single_line(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


async def _unique_request_username(session: AsyncSession, name: str, day: date) -> str:
    """Libero sia nei pannelli sia fra le altre richieste di nuova linea."""
    base = username_for(name, day)
    candidate = base
    suffix = 2
    while True:
        panel = await session.scalar(
            select(PanelLine.id).where(PanelLine.username_key == candidate)
        )
        request = await session.scalar(
            select(NewLineRequest.id).where(NewLineRequest.username == candidate)
        )
        if panel is None and request is None:
            return candidate
        candidate = f"{base}{suffix}"
        suffix += 1
        if suffix > 99:
            raise NewLineRequestError("troppi username simili: controlla il pannello.")


async def create_new_line_request(
    session: AsyncSession,
    *,
    reseller_id: int,
    requested_by: int,
    name: str,
    months: int,
    service: Service,
    note: str | None,
    today: date,
    origin_chat_id: int | None = None,
    origin_message_id: int | None = None,
) -> NewLineRequest:
    cleaned = _single_line(name)
    if cleaned is None or len(cleaned) < 2:
        raise NewLineRequestError("scrivi nome e cognome del cliente.")
    if len(cleaned) > NAME_MAX:
        raise NewLineRequestError(f"il nome supera {NAME_MAX} caratteri.")

    if not MIN_MONTHS <= months <= MAX_MONTHS:
        raise NewLineRequestError(f"i mesi devono stare tra {MIN_MONTHS} e {MAX_MONTHS}")
    if service not in (Service.LIVE, Service.LIVE_VOD):
        raise NewLineRequestError("servizio non valido: scegli Live o Live + VOD.")

    cleaned_note = _single_line(note)
    if cleaned_note is not None and len(cleaned_note) > NOTES_MAX:
        raise NewLineRequestError(f"la nota supera {NOTES_MAX} caratteri.")

    reseller = await session.get(Reseller, reseller_id)
    if reseller is None or not reseller.active:
        raise NewLineRequestError("rivenditore non trovato o non attivo.")

    username = await _unique_request_username(session, cleaned, today)
    request = NewLineRequest(
        reseller_id=reseller.id,
        requested_by_telegram_id=requested_by,
        name=cleaned,
        username=username,
        months=months,
        service=service,
        note=cleaned_note,
        status=RequestStatus.PENDING,
        origin_chat_id=origin_chat_id,
        origin_message_id=origin_message_id,
    )
    session.add(request)
    await session.flush()
    return request


async def pending_new_line_requests(session: AsyncSession) -> list[NewLineRequest]:
    return list(
        (
            await session.scalars(
                select(NewLineRequest)
                .where(NewLineRequest.status == RequestStatus.PENDING)
                .order_by(NewLineRequest.id)
            )
        ).all()
    )


async def approve_new_line_request(
    session: AsyncSession,
    *,
    request_id: int,
    price_cents: int,
    approved_by: int | None,
    today: date,
) -> tuple[NewLineRequest, Customer, Line, str, str]:
    """Conferma la richiesta: cliente nuovo, linea e credenziali da copiare."""
    request = await session.get(NewLineRequest, request_id)
    if request is None or request.status is not RequestStatus.PENDING:
        raise NewLineRequestError("richiesta non trovata o già gestita.")
    if price_cents < 0:
        raise NewLineRequestError("il prezzo non può essere negativo.")

    reseller = await session.get(Reseller, request.reseller_id)
    if reseller is None or not reseller.active:
        raise NewLineRequestError("rivenditore non trovato o non attivo.")

    # L'ID è stato congelato alla richiesta: cambia solo se nel frattempo una
    # riga di pannello ha preso lo stesso username.
    username = await ensure_username(session, request.username)
    request.username = username

    customer, line = await create_customer_and_line(
        session,
        LineDraft(
            name=request.name,
            service=request.service,
            monthly_price_cents=price_cents,
            months=request.months,
            reseller_id=reseller.id,
            notes=request.note,
            start_on=today,
        ),
        today,
    )
    request.status = RequestStatus.APPROVED
    request.line_id = line.id
    request.resolved_at = utcnow()
    request.resolved_by_telegram_id = approved_by
    await session.flush()
    return request, customer, line, username, password()


async def reject_new_line_request(
    session: AsyncSession, *, request_id: int, rejected_by: int | None
) -> NewLineRequest:
    request = await session.get(NewLineRequest, request_id)
    if request is None or request.status is not RequestStatus.PENDING:
        raise NewLineRequestError("richiesta non trovata o già gestita.")
    request.status = RequestStatus.REJECTED
    request.resolved_at = utcnow()
    request.resolved_by_telegram_id = rejected_by
    await session.flush()
    return request
