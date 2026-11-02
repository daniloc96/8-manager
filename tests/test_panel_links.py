from __future__ import annotations

from datetime import date

from app.models import Customer, Line, PanelLine, Platform, Service
from app.services.panel_links import link_panel_line


async def test_link_and_unlink_panel_line(session) -> None:
    customer = Customer(name="Mario Rossi", search_key="mario rossi")
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2026, 1, 1),
        expires_on=date(2026, 12, 1),
        anchor_day=1,
    )
    panel_line = PanelLine(
        platform=Platform.VOD_NEWVOD,
        username="mario-vod",
        username_key="mario-vod",
        expires_on=date(2026, 12, 1),
    )
    session.add_all([customer, line, panel_line])
    await session.flush()

    linked_line, linked_panel = await link_panel_line(
        session,
        platform=Platform.VOD_NEWVOD,
        username="mario-vod",
        line_id=line.id,
    )
    assert linked_panel.line_id == line.id
    assert linked_line.service is Service.LIVE_VOD

    unlinked_line, unlinked_panel = await link_panel_line(
        session,
        platform=Platform.VOD_NEWVOD,
        username="mario-vod",
        line_id=line.id,
        unlink=True,
    )
    assert unlinked_panel.line_id is None
    assert unlinked_line.service is Service.LIVE
