from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select

from app.models import (
    Customer,
    Line,
    PanelLine,
    Platform,
    Renewal,
    RenewalSource,
    Reseller,
    Service,
)
from app.services.queries import (
    due_groups,
    get_line,
    income_report,
    lines_of_reseller,
    panel_for,
    search_lines,
)
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)


def make_line(
    customer: Customer,
    expires_on: date,
    *,
    service: Service = Service.LIVE_VOD,
    label: str | None = None,
    reseller: Reseller | None = None,
    price: int = 500,
) -> Line:
    return Line(
        customer=customer,
        service=service,
        monthly_price_cents=price,
        started_on=date(2025, 1, 1),
        expires_on=expires_on,
        anchor_day=1,
        label=label,
        reseller=reseller,
    )


async def _seed(session):
    mario = Customer(
        name="Mario Caterino",
        alias="Mario il rosso",
        search_key=customer_search_key("Mario Caterino", "Mario il rosso"),
    )
    anna = Customer(name="Anna Bianchi", search_key=customer_search_key("Anna Bianchi"))
    reseller = Reseller(name="Luca", name_key="luca")
    session.add_all([mario, anna, reseller])
    await session.flush()

    lines = [
        make_line(mario, date(2026, 10, 20), label="Linea 1"),
        make_line(mario, TODAY, label="Linea 2", reseller=reseller),
        make_line(anna, date(2026, 11, 4), service=Service.LIVE),
        make_line(anna, date(2027, 5, 1), service=Service.LIVE),
    ]
    session.add_all(lines)
    await session.flush()

    session.add_all(
        [
            PanelLine(
                platform=Platform.LIVE_HALLO,
                username="MarioC115032019",
                username_key="marioc115032019",
                line_id=lines[0].id,
                expires_on=date(2026, 10, 20),
            ),
            PanelLine(
                platform=Platform.VOD_NEWVOD,
                username="MarioC1150319",
                username_key="marioc1150319",
                line_id=lines[0].id,
                expires_on=date(2026, 10, 20),
            ),
        ]
    )
    await session.flush()
    return mario, anna, reseller, lines


async def test_due_groups_skip_lines_with_active_exception(session) -> None:
    from app.models import ReviewException
    from app.services.queries import muted_line_ids

    _mario, _anna, _reseller, lines = await _seed(session)
    session.add(ReviewException(line_id=lines[0].id, reason="linea personale"))
    session.add(
        ReviewException(line_id=lines[1].id, reason="si decide dopo", review_on=date(2026, 12, 19))
    )
    await session.flush()

    groups = await due_groups(session, TODAY, warn_days=3)

    assert [line.id for line in groups.expired] == []
    assert [line.id for line in groups.today] == []
    assert await muted_line_ids(session, TODAY) == {lines[0].id, lines[1].id}

    # alla data di revisione l'eccezione scade e la linea torna visibile
    later = await due_groups(session, date(2026, 12, 19), warn_days=3)
    assert lines[1].id in [line.id for line in later.expired]


async def test_due_groups_split_expired_today_and_soon(session) -> None:
    _mario, _anna, _reseller, lines = await _seed(session)

    groups = await due_groups(session, TODAY, warn_days=3)

    assert [line.id for line in groups.expired] == [lines[0].id]
    assert [line.id for line in groups.today] == [lines[1].id]
    assert [line.id for line in groups.soon] == [lines[2].id]
    assert groups.total == 3


async def test_search_matches_alias_label_and_panel_username(session) -> None:
    _mario, _anna, _reseller, lines = await _seed(session)

    by_alias = await search_lines(session, "rosso")
    # l'alias è del cliente: valgono tutte le sue linee
    assert {line.id for line in by_alias} == {lines[0].id, lines[1].id}

    by_label = await search_lines(session, "linea 2")
    assert [line.id for line in by_label] == [lines[1].id]

    by_username = await search_lines(session, "marioc1150319")
    assert [line.id for line in by_username] == [lines[0].id]

    by_customer = await search_lines(session, "anna")
    assert {line.id for line in by_customer} == {lines[2].id, lines[3].id}


async def test_search_ignores_accents_and_case(session) -> None:
    customer = Customer(name="Nicola Cafè", search_key=customer_search_key("Nicola Cafè"))
    session.add(customer)
    await session.flush()
    line = make_line(customer, date(2027, 1, 1))
    session.add(line)
    await session.flush()

    assert [item.id for item in await search_lines(session, "cafe")] == [line.id]
    assert [item.id for item in await search_lines(session, "CAFÈ")] == [line.id]
    assert await search_lines(session, "") == []


async def test_get_line_hides_deleted(session) -> None:
    customer = Customer(name="X", search_key="x")
    session.add(customer)
    await session.flush()
    line = make_line(customer, date(2027, 1, 1))
    session.add(line)
    await session.flush()

    assert await get_line(session, line.id) is not None
    line.deleted_at = datetime.now(UTC)
    await session.flush()
    assert await get_line(session, line.id) is None


async def test_income_groups_by_reseller(session) -> None:
    _mario, _anna, _reseller, lines = await _seed(session)
    session.add_all(
        [
            Renewal(
                line_id=lines[1].id,
                months=3,
                monthly_price_cents=500,
                total_cents=1500,
                period_start=date(2026, 11, 1),
                period_end=date(2027, 2, 1),
                source=RenewalSource.CARD,
                created_at=datetime(2026, 11, 2, 10, 0, tzinfo=UTC),
            ),
            Renewal(
                line_id=lines[3].id,
                months=1,
                monthly_price_cents=400,
                total_cents=400,
                period_start=date(2026, 11, 1),
                period_end=date(2026, 12, 1),
                source=RenewalSource.CARD,
                created_at=datetime(2026, 11, 1, 10, 0, tzinfo=UTC),
            ),
            Renewal(
                line_id=lines[0].id,
                months=1,
                monthly_price_cents=500,
                total_cents=500,
                period_start=date(2026, 10, 1),
                period_end=date(2026, 11, 1),
                source=RenewalSource.CARD,
                created_at=datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
            ),
        ]
    )
    await session.flush()

    report = await income_report(session, 2026, 11)

    assert report.total_cents == 1900
    by_name = {row.reseller: row for row in report.rows}
    assert by_name["Luca"].total_cents == 1500
    assert by_name[None].total_cents == 400


async def test_lines_of_reseller_filters(session) -> None:
    _mario, _anna, _reseller, lines = await _seed(session)
    reseller = lines[1].reseller

    mine = await lines_of_reseller(session, reseller.id)
    assert [line.id for line in mine] == [lines[1].id]

    assert panel_for(mine[0], Platform.LIVE_HALLO) is None
    assert panel_for(lines[0], Platform.VOD_NEWVOD) is not None


async def test_active_lines_count(session) -> None:
    from sqlalchemy import func

    _mario, _anna, _reseller, lines = await _seed(session)
    lines[0].deleted_at = datetime.now(UTC)
    await session.flush()

    active = await session.scalar(select(func.count(Line.id)).where(Line.deleted_at.is_(None)))
    assert active == 3


async def test_income_report_includes_portfolio_value(session) -> None:
    """Il parco conta le linee attive e somma i prezzi, ignorando le disattivate."""
    _mario, _anna, _reseller, lines = await _seed(session)
    lines[2].deleted_at = datetime.now(UTC)
    await session.flush()

    report = await income_report(session, 2026, 11)

    # 4 linee da 500 centesimi meno quella disattivata.
    assert report.active_lines == 3
    assert report.monthly_cents == 1500
    # Il parco non dipende dai rinnovi: nessun rinnovo in questo mese.
    assert report.rows == []
    assert report.total_cents == 0


async def test_portfolio_counts_customers_not_channels(session) -> None:
    """Un cliente con Live e VOD non vale doppio: conta una volta sola."""
    await _seed(session)

    report = await income_report(session, 2026, 11)

    assert report.active_lines == 4
    assert report.active_customers == 2, "due clienti, quattro linee"
    assert report.monthly_cents == 2000


async def test_expired_lines_are_not_income(session) -> None:
    """Una linea scaduta non è guadagno: resta per il rinnovo, fuori dal parco."""
    _mario, _anna, _reseller, lines = await _seed(session)
    lines[0].expires_on = date(2026, 10, 20)
    await session.flush()

    report = await income_report(session, 2026, 11, today=TODAY)

    assert report.active_lines == 3
    assert report.monthly_cents == 1500
    assert report.expired_lines == 1
    assert report.expired_cents == 500
    # senza `today` non si filtra: la vista anagrafica vede tutte le attive
    plain = await income_report(session, 2026, 11)
    assert plain.active_lines == 4
    assert plain.expired_lines == 0


async def test_client_with_only_expired_lines_is_not_counted(session) -> None:
    _mario, anna, _reseller, lines = await _seed(session)
    for line in lines:
        if line.customer_id == anna.id:
            line.expires_on = date(2026, 1, 1)
    await session.flush()

    report = await income_report(session, 2026, 11, today=TODAY)

    assert report.active_customers == 1, "Anna non paga più: fuori dal conteggio"


async def test_income_report_portfolio_survives_empty_month(session) -> None:
    await _seed(session)

    report = await income_report(session, 2030, 1)

    assert report.active_lines == 4
    assert report.monthly_cents == 2000


async def test_restore_line_reactivates(session) -> None:
    from datetime import UTC, datetime

    from app.services.queries import restore_line

    customer = Customer(name="X", search_key="x")
    session.add(customer)
    await session.flush()
    line = make_line(customer, date(2027, 1, 1))
    session.add(line)
    await session.flush()
    line.deleted_at = datetime.now(UTC)
    await session.flush()
    assert await get_line(session, line.id) is None

    restored = await restore_line(session, line.id)

    assert restored is not None
    assert restored.deleted_at is None
    assert await get_line(session, line.id) is not None
    assert await restore_line(session, 9999) is None
