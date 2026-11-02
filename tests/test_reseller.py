from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.bot.auth import resolve_access
from app.models import (
    Customer,
    Line,
    Renewal,
    RequestStatus,
    Reseller,
    Service,
)
from app.services.queries import due_groups, search_lines
from app.services.requests import (
    RequestError,
    approve_request,
    authorize_reseller,
    create_request,
    pending_requests,
    reject_request,
)
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)
OWNER_ID = 42


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", str(OWNER_ID))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed(session):
    reseller = Reseller(name="Luca", name_key="luca", telegram_user_id=777)
    other = Reseller(name="Sara", name_key="sara", telegram_user_id=888)
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    session.add_all([reseller, other, customer])
    await session.flush()

    mine = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=TODAY,
        anchor_day=1,
        reseller_id=reseller.id,
    )
    theirs = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=400,
        started_on=date(2025, 1, 1),
        expires_on=TODAY,
        anchor_day=1,
        reseller_id=other.id,
    )
    session.add_all([mine, theirs])
    await session.flush()
    return reseller, other, mine, theirs


async def test_resolve_access_roles(session) -> None:
    await _seed(session)

    assert (await resolve_access(session, OWNER_ID)).kind == "owner"
    reseller_access = await resolve_access(session, 777)
    assert reseller_access.kind == "reseller"
    assert reseller_access.is_reseller
    assert (await resolve_access(session, 999)).kind == "none"
    assert (await resolve_access(session, None)).kind == "none"


async def test_reseller_sees_only_own_lines(session) -> None:
    reseller, _other, mine, theirs = await _seed(session)

    groups = await due_groups(session, TODAY, reseller_id=reseller.id)
    assert [line.id for line in groups.today] == [mine.id]

    results = await search_lines(session, "mario", reseller_id=reseller.id)
    assert [line.id for line in results] == [mine.id]

    owner_groups = await due_groups(session, TODAY)
    assert {line.id for line in owner_groups.today} == {mine.id, theirs.id}


async def test_request_requires_valid_months(session) -> None:
    _reseller, _other, mine, _theirs = await _seed(session)

    with pytest.raises(RequestError):
        await create_request(session, line_id=mine.id, requested_by=777, months=0)

    with pytest.raises(RequestError):
        await create_request(session, line_id=9999, requested_by=777, months=1)


async def test_approve_request_registers_renewal(session) -> None:
    _reseller, _other, mine, _theirs = await _seed(session)
    request = await create_request(
        session, line_id=mine.id, requested_by=777, months=2, note="rinnovo standard"
    )

    approved, line = await approve_request(
        session, request_id=request.id, months=3, today=TODAY, approved_by=OWNER_ID
    )

    assert approved.status is RequestStatus.APPROVED
    assert approved.months == 3
    assert approved.renewal_id is not None
    # ancora al giorno 1: 02/11 + 3 mesi = 01/02
    assert line.expires_on == date(2027, 2, 1)
    renewals = list((await session.scalars(select(Renewal))).all())
    assert len(renewals) == 1
    assert renewals[0].total_cents == 1500
    assert renewals[0].note == "rinnovo standard"
    assert await pending_requests(session) == []


async def test_request_cannot_be_approved_twice(session) -> None:
    _reseller, _other, mine, _theirs = await _seed(session)
    request = await create_request(session, line_id=mine.id, requested_by=777, months=1)
    await approve_request(session, request_id=request.id, months=1, today=TODAY, approved_by=1)

    with pytest.raises(RequestError):
        await approve_request(session, request_id=request.id, months=1, today=TODAY, approved_by=1)


async def test_reject_request(session) -> None:
    _reseller, _other, mine, _theirs = await _seed(session)
    request = await create_request(session, line_id=mine.id, requested_by=777, months=1)

    rejected = await reject_request(session, request_id=request.id, rejected_by=OWNER_ID)

    assert rejected.status is RequestStatus.REJECTED
    assert await session.scalar(select(Renewal)) is None
    assert await pending_requests(session) == []


async def test_authorize_reseller_creates_and_links(session) -> None:
    existing = Reseller(name="Sara", name_key="sara")
    session.add(existing)
    await session.flush()

    created = await authorize_reseller(session, telegram_user_id=1234, name="Nuovo Tizio")
    assert created.telegram_user_id == 1234
    assert created.active is True

    linked = await authorize_reseller(session, telegram_user_id=4321, name="Sara")
    assert linked.id == existing.id
    assert linked.telegram_user_id == 4321

    again = await authorize_reseller(session, telegram_user_id=1234, name="Nuovo Tizio")
    assert again.id == created.id


async def test_pending_requests_lists_only_pending(session) -> None:
    _reseller, _other, mine, theirs = await _seed(session)
    first = await create_request(session, line_id=mine.id, requested_by=777, months=1)
    second = await create_request(session, line_id=theirs.id, requested_by=888, months=2)
    await reject_request(session, request_id=first.id, rejected_by=OWNER_ID)

    pending = await pending_requests(session)
    assert [request.id for request in pending] == [second.id]
