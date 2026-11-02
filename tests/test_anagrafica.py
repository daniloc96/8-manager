from __future__ import annotations

from datetime import date

import pytest

from app.models import Customer, Line, PanelLine, Platform, Service
from app.services.anagrafica import (
    AnagraficaError,
    set_customer_alias,
    set_line_label,
    set_line_notes,
    set_panel_field,
)
from app.services.queries import search_lines
from app.services.text import customer_search_key


async def _seed(session):
    customer = Customer(name="Mario Caterino", search_key=customer_search_key("Mario Caterino"))
    session.add(customer)
    await session.flush()
    first = Line(
        customer=customer,
        service=Service.LIVE_VOD,
        monthly_price_cents=500,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        label="Linea 1",
    )
    second = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=600,
        started_on=date(2025, 1, 1),
        expires_on=date(2027, 1, 1),
        anchor_day=1,
        label="Linea 2",
    )
    session.add_all([first, second])
    await session.flush()
    panel = PanelLine(
        platform=Platform.LIVE_HALLO,
        username="mario2026",
        username_key="mario2026",
        line_id=first.id,
        access_code=None,
    )
    session.add(panel)
    await session.flush()
    return customer, first, second, panel


async def test_alias_change_updates_search_key(session) -> None:
    customer, _first, _second, _panel = await _seed(session)

    updated, changed = await set_customer_alias(session, customer.id, "  Mario il  rosso ")

    assert changed is True
    assert updated.alias == "Mario il rosso"
    assert updated.search_key == customer_search_key("Mario Caterino", "Mario il rosso")

    # La ricerca libera deve trovare l'alias nuovo.
    found = await search_lines(session, "rosso", today=date(2026, 11, 2))
    assert {line.id for line in found} == {_first.id, _second.id}


async def test_alias_unchanged_reports_no_change(session) -> None:
    customer, _first, _second, _panel = await _seed(session)
    await set_customer_alias(session, customer.id, "Pinco")

    _customer, changed = await set_customer_alias(session, customer.id, "Pinco")

    assert changed is False


async def test_alias_can_be_cleared(session) -> None:
    customer, _first, _second, _panel = await _seed(session)
    await set_customer_alias(session, customer.id, "Pinco")

    updated, changed = await set_customer_alias(session, customer.id, None)

    assert changed is True
    assert updated.alias is None
    assert updated.search_key == customer_search_key("Mario Caterino")


async def test_label_duplicate_is_rejected(session) -> None:
    _customer, first, _second, _panel = await _seed(session)

    with pytest.raises(AnagraficaError) as excinfo:
        await set_line_label(session, first.id, "Linea 2")

    assert "Linea 2" in str(excinfo.value)
    assert first.label == "Linea 1"


async def test_label_can_change_and_be_cleared(session) -> None:
    _customer, first, _second, _panel = await _seed(session)

    changed_line, changed = await set_line_label(session, first.id, "Linea 9")
    assert changed is True
    assert changed_line.label == "Linea 9"

    cleared, changed = await set_line_label(session, first.id, None)
    assert changed is True
    assert cleared.label is None

    # Una seconda linea senza etichetta non è un conflitto: l'indice unico
    # vale solo per le etichette non nulle.
    _line, changed = await set_line_label(session, _second.id, "")
    assert changed is True


async def test_label_same_value_is_noop(session) -> None:
    _customer, first, _second, _panel = await _seed(session)

    _line, changed = await set_line_label(session, first.id, " Linea 1 ")

    assert changed is False


async def test_notes_are_saved_and_truncation_checked(session) -> None:
    _customer, first, _second, _panel = await _seed(session)

    line, changed = await set_line_notes(session, first.id, "  rinnovo a mano  ")
    assert changed is True
    assert line.notes == "rinnovo a mano"

    with pytest.raises(AnagraficaError):
        await set_line_notes(session, first.id, "x" * 501)


async def test_panel_field_updates_only_allowed_columns(session) -> None:
    _customer, first, _second, _panel = await _seed(session)

    updated, changed = await set_panel_field(
        session, first.id, Platform.LIVE_HALLO, "access_code", " 1234ABCD "
    )
    assert changed is True
    assert updated.access_code == "1234ABCD"

    with pytest.raises(AnagraficaError):
        await set_panel_field(session, first.id, Platform.LIVE_HALLO, "username", "altro")


async def test_panel_field_without_panel_row_is_rejected(session) -> None:
    _customer, first, _second, _panel = await _seed(session)

    with pytest.raises(AnagraficaError) as excinfo:
        await set_panel_field(session, first.id, Platform.VOD_NEWVOD, "external_id", "9")

    assert "pannello" in str(excinfo.value)


async def test_audit_log_written_for_alias(session) -> None:
    from sqlalchemy import select

    from app.models import AuditLog

    customer, _first, _second, _panel = await _seed(session)
    await set_customer_alias(session, customer.id, "Pinco")

    entry = await session.scalar(select(AuditLog).where(AuditLog.action == "customer_alias_set"))
    assert entry is not None
    assert entry.payload["new"] == "Pinco"
