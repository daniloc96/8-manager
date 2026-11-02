from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.bot.auth import resolve_access
from app.models import Customer, Line, Reseller, Service
from app.services.requests import (
    RequestError,
    link_group,
    reseller_by_name,
    unlink_group,
)
from app.services.text import customer_search_key

OWNER_ID = 42
GROUP_ID = -1001234567890


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OWNER_TELEGRAM_ID", str(OWNER_ID))
    from app.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _seed(session):
    mario = Reseller(name="Mario", name_key="mario", telegram_user_id=777)
    sara = Reseller(name="Sara", name_key="sara", telegram_user_id=888)
    customer = Customer(name="Mario Rossi", search_key=customer_search_key("Mario Rossi"))
    session.add_all([mario, sara, customer])
    await session.flush()
    line = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=700,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        reseller_id=mario.id,
    )
    session.add(line)
    await session.flush()
    return mario, sara, line


async def test_group_access_reads_reseller_lines(session) -> None:
    mario, _sara, _line = await _seed(session)
    await link_group(session, reseller_id=mario.id, chat_id=GROUP_ID, title="Gruppo Mario")

    stranger_in_group = await resolve_access(session, 999, chat_id=GROUP_ID, chat_type="supergroup")
    assert stranger_in_group.kind == "reseller"
    assert stranger_in_group.reseller_id == mario.id
    assert stranger_in_group.via_group is True
    assert stranger_in_group.allowed is True
    assert stranger_in_group.can_act is False


async def test_group_does_not_grant_write_access(session) -> None:
    mario, _sara, _line = await _seed(session)
    await link_group(session, reseller_id=mario.id, chat_id=GROUP_ID)

    reseller_in_group = await resolve_access(session, 777, chat_id=GROUP_ID, chat_type="group")
    # l'identità personale vince sul gruppo: può agire
    assert reseller_in_group.is_reseller
    assert reseller_in_group.via_group is False
    assert reseller_in_group.can_act is True


async def test_unregistered_group_has_no_access(session) -> None:
    await _seed(session)

    access = await resolve_access(session, 999, chat_id=-1009999999999, chat_type="group")
    assert access.kind == "none"
    assert access.allowed is False


async def test_owner_access_ignores_group(session) -> None:
    await _seed(session)

    access = await resolve_access(session, OWNER_ID, chat_id=GROUP_ID, chat_type="group")
    assert access.is_owner
    assert access.can_act is True


async def test_link_group_moves_to_new_reseller(session) -> None:
    mario, sara, _line = await _seed(session)
    await link_group(session, reseller_id=mario.id, chat_id=GROUP_ID, title="Mario")
    await link_group(session, reseller_id=sara.id, chat_id=GROUP_ID, title="Sara")

    refreshed_mario = await session.get(Reseller, mario.id, populate_existing=True)
    refreshed_sara = await session.get(Reseller, sara.id, populate_existing=True)
    assert refreshed_mario.group_chat_id is None
    assert refreshed_sara.group_chat_id == GROUP_ID
    assert refreshed_sara.group_title == "Sara"


async def test_unlink_group(session) -> None:
    mario, _sara, _line = await _seed(session)
    await link_group(session, reseller_id=mario.id, chat_id=GROUP_ID)

    released = await unlink_group(session, chat_id=GROUP_ID)

    assert released is not None
    assert released.name == "Mario"
    assert await unlink_group(session, chat_id=GROUP_ID) is None
    access = await resolve_access(session, 999, chat_id=GROUP_ID, chat_type="group")
    assert access.allowed is False


async def test_link_group_requires_existing_reseller(session) -> None:
    await _seed(session)
    with pytest.raises(RequestError):
        await link_group(session, reseller_id=9999, chat_id=GROUP_ID)


async def test_reseller_by_name_normalizes(session) -> None:
    await _seed(session)
    assert (await reseller_by_name(session, "MARIO")).id == 1
    assert await reseller_by_name(session, "Nessuno") is None


async def test_group_chat_id_is_unique(session) -> None:
    mario, sara, _line = await _seed(session)
    await link_group(session, reseller_id=mario.id, chat_id=GROUP_ID)

    async with session.begin_nested():
        sara.group_chat_id = GROUP_ID
        with pytest.raises(IntegrityError):
            await session.flush()
    await session.rollback()
