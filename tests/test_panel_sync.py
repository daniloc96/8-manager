from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    AuditLog,
    Customer,
    Line,
    PanelLine,
    Platform,
    Renewal,
    RenewalSource,
    Service,
    SyncRun,
    SyncStatus,
)
from app.panels.base import PanelLine as PanelRow
from app.panels.sync import link_import_reconcile, run_sync, upsert_rows


def row(
    platform: Platform,
    username: str,
    *,
    note: str = "Mario Rossi",
    expires_on: date = date(2026, 12, 15),
) -> PanelRow:
    return PanelRow(
        platform=platform,
        external_id=f"{platform.value}-{username}",
        username=username,
        password="password",
        access_code=username,
        expires_on=expires_on,
        status="active",
        note=note,
        max_connections=1,
        raw={"username": username},
    )


class FakeAdapter:
    def __init__(self, platform: Platform, rows: list[PanelRow]) -> None:
        self.platform = platform
        self._rows = rows
        self.closed = False

    async def fetch_lines(self) -> list[PanelRow]:
        return list(self._rows)

    async def aclose(self) -> None:
        self.closed = True


class BrokenAdapter:
    def __init__(self, platform: Platform) -> None:
        self.platform = platform
        self.closed = False

    async def fetch_lines(self) -> list[PanelRow]:
        raise RuntimeError("pannello irraggiungibile")

    async def aclose(self) -> None:
        self.closed = True


async def test_upsert_is_idempotent_and_reports_missing(session) -> None:
    first = await upsert_rows(
        session,
        Platform.LIVE_HALLO,
        [
            row(Platform.LIVE_HALLO, "mario", expires_on=date(2026, 12, 1)),
            row(Platform.LIVE_HALLO, "anna", note="Anna Bianchi"),
        ],
    )
    assert (first.created, first.updated) == (2, 0)

    second = await upsert_rows(
        session,
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", expires_on=date(2027, 1, 1))],
    )
    assert (second.created, second.updated) == (0, 1)
    assert [record.username for record in second.missing] == ["anna"]

    records = list(
        (
            await session.scalars(
                select(PanelLine)
                .where(PanelLine.platform == Platform.LIVE_HALLO)
                .order_by(PanelLine.username)
            )
        ).all()
    )
    assert records[0].username == "anna"
    assert records[1].expires_on == date(2027, 1, 1)


async def test_import_pairs_live_and_vod(session) -> None:
    for platform, rows in {
        Platform.LIVE_HALLO: [
            row(Platform.LIVE_HALLO, "mario", expires_on=date(2026, 12, 15)),
            row(Platform.LIVE_HALLO, "anna", note="Anna Bianchi"),
        ],
        Platform.VOD_NEWVOD: [
            row(Platform.VOD_NEWVOD, "mario", expires_on=date(2026, 12, 20)),
            row(Platform.VOD_NEWVOD, "solo", note="Solo Vod"),
        ],
        Platform.LIVE_8TVPLUS: [
            row(Platform.LIVE_8TVPLUS, "CODE1", note="Luca Verdi"),
        ],
    }.items():
        await upsert_rows(session, platform, rows)

    result = await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_HALLO, Platform.VOD_NEWVOD, Platform.LIVE_8TVPLUS],
        today=date(2026, 11, 1),
        import_missing=True,
    )

    lines = list((await session.scalars(select(Line).order_by(Line.id))).all())
    assert len(lines) == 3

    paired = next(line for line in lines if line.customer.name == "Mario Rossi")
    assert paired.service is Service.LIVE_VOD
    assert paired.expires_on == date(2026, 12, 15)
    assert paired.monthly_price_cents == 0
    assert paired.notes and "prezzo da impostare" in paired.notes

    live_only = next(line for line in lines if line.customer.name == "Anna Bianchi")
    assert live_only.service is Service.LIVE

    eightplus = next(line for line in lines if line.customer.name == "Luca Verdi")
    assert eightplus.service is Service.LIVE

    panel_rows = list((await session.scalars(select(PanelLine))).all())
    by_username = {record.username: record for record in panel_rows}
    assert by_username["mario"].line_id == paired.id
    assert by_username["solo"].line_id is None
    assert by_username["CODE1"].line_id == eightplus.id

    assert result.imported.get(Platform.LIVE_HALLO) == 2
    assert result.imported.get(Platform.VOD_NEWVOD) == 1
    assert result.imported.get(Platform.LIVE_8TVPLUS) == 1
    assert any("VOD senza Live" in review.reason for review in result.reviews)


async def test_import_keeps_vod_orphan_on_note_mismatch(session) -> None:
    await upsert_rows(
        session,
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", note="Mario Rossi")],
    )
    await upsert_rows(
        session,
        Platform.VOD_NEWVOD,
        [row(Platform.VOD_NEWVOD, "mario", note="Mario Bianchi")],
    )

    result = await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_HALLO, Platform.VOD_NEWVOD],
        today=date(2026, 11, 1),
        import_missing=True,
    )

    lines = list((await session.scalars(select(Line))).all())
    assert len(lines) == 1
    assert lines[0].service is Service.LIVE

    panel_rows = {
        (record.platform, record.username): record
        for record in (await session.scalars(select(PanelLine))).all()
    }
    live = panel_rows[(Platform.LIVE_HALLO, "mario")]
    vod = panel_rows[(Platform.VOD_NEWVOD, "mario")]
    assert live.line_id == lines[0].id
    assert vod.line_id is None
    assert any("note diverse" in review.reason for review in result.reviews)


async def test_import_pairs_fuzzy_notes(session) -> None:
    await upsert_rows(
        session,
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", note="Francesco Collega")],
    )
    await upsert_rows(
        session,
        Platform.VOD_NEWVOD,
        [row(Platform.VOD_NEWVOD, "mario", note="Francesco Collega Mario")],
    )

    await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_HALLO, Platform.VOD_NEWVOD],
        today=date(2026, 11, 1),
        import_missing=True,
    )

    lines = list((await session.scalars(select(Line))).all())
    assert len(lines) == 1
    assert lines[0].service is Service.LIVE_VOD


async def test_import_assigns_labels_to_shared_customer(session) -> None:
    await upsert_rows(
        session,
        Platform.LIVE_8TVPLUS,
        [row(Platform.LIVE_8TVPLUS, "CODE1", note="Mario Rossi")],
    )
    await upsert_rows(
        session,
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", note="Mario Rossi")],
    )
    await upsert_rows(
        session,
        Platform.VOD_NEWVOD,
        [row(Platform.VOD_NEWVOD, "mario", note="Mario Rossi")],
    )

    await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_8TVPLUS, Platform.LIVE_HALLO, Platform.VOD_NEWVOD],
        today=date(2026, 11, 1),
        import_missing=True,
    )

    customers = list((await session.scalars(select(Customer))).all())
    assert len(customers) == 1
    lines = list((await session.scalars(select(Line).order_by(Line.id))).all())
    assert len(lines) == 2
    assert {line.label for line in lines} == {"Linea 1", "Linea 2"}


async def test_reconcile_takes_panel_expiry(session) -> None:
    customer = Customer(name="Mario Rossi", search_key="mario rossi")
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2026, 1, 1),
        expires_on=date(2026, 12, 1),
        anchor_day=1,
    )
    session.add_all([customer, line])
    await session.flush()
    session.add(
        PanelLine(
            line_id=line.id,
            platform=Platform.LIVE_HALLO,
            username="mario",
            username_key="mario",
            expires_on=date(2026, 12, 1),
        )
    )
    await session.flush()

    await upsert_rows(
        session,
        Platform.LIVE_HALLO,
        [row(Platform.LIVE_HALLO, "mario", expires_on=date(2027, 3, 1))],
    )
    result = await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_HALLO],
        today=date(2026, 11, 1),
        import_missing=False,
    )

    assert line.expires_on == date(2027, 3, 1)
    assert result.expiry_updates.get(Platform.LIVE_HALLO) == 1

    audit = list(
        (await session.scalars(select(AuditLog).where(AuditLog.action == "panel_expiry"))).all()
    )
    assert len(audit) == 1
    assert audit[0].entity_id == line.id

    renewals = list((await session.scalars(select(Renewal))).all())
    assert len(renewals) == 1
    assert renewals[0].months == 3
    assert renewals[0].period_start == date(2026, 12, 1)
    assert renewals[0].period_end == date(2027, 3, 1)
    assert renewals[0].monthly_price_cents == 500
    assert renewals[0].total_cents == 1500
    assert renewals[0].source is RenewalSource.SYNC


async def test_eightplus_day_jump_is_reviewed_not_guessed(session) -> None:
    customer = Customer(name="Luca Verdi", search_key="luca verdi")
    line = Line(
        customer=customer,
        service=Service.LIVE,
        monthly_price_cents=500,
        started_on=date(2026, 1, 1),
        expires_on=date(2026, 12, 15),
        anchor_day=15,
    )
    session.add_all([customer, line])
    await session.flush()
    session.add(
        PanelLine(
            line_id=line.id,
            platform=Platform.LIVE_8TVPLUS,
            username="CODE1",
            username_key="code1",
            expires_on=date(2026, 12, 15),
        )
    )
    await session.flush()

    await upsert_rows(
        session,
        Platform.LIVE_8TVPLUS,
        [row(Platform.LIVE_8TVPLUS, "CODE1", note="Luca Verdi", expires_on=date(2027, 1, 14))],
    )
    result = await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_8TVPLUS],
        today=date(2026, 11, 1),
        import_missing=False,
    )

    assert line.expires_on == date(2027, 1, 14)
    assert result.renewals.get(Platform.LIVE_8TVPLUS, 0) == 0
    assert any("non a mesi interi" in review.reason for review in result.reviews)
    assert (await session.scalars(select(Renewal))).all() == []


async def test_expired_vod_orphan_is_historical(session) -> None:
    await upsert_rows(
        session,
        Platform.VOD_NEWVOD,
        [row(Platform.VOD_NEWVOD, "vecchio", note="Vecchio", expires_on=date(2025, 1, 1))],
    )

    result = await link_import_reconcile(
        session,
        platforms=[Platform.VOD_NEWVOD],
        today=date(2026, 11, 1),
        import_missing=True,
    )

    assert (await session.scalars(select(Line))).all() == []
    assert result.historical.get(Platform.VOD_NEWVOD) == 1
    assert result.reviews == []


async def test_date_mismatch_only_when_service_is_partial(session) -> None:
    def add_line(name: str, live_date: date, vod_date: date) -> None:
        customer = Customer(name=name, search_key=name.lower())
        line = Line(
            customer=customer,
            service=Service.LIVE_VOD,
            monthly_price_cents=500,
            started_on=date(2025, 1, 1),
            expires_on=min(live_date, vod_date),
            anchor_day=1,
        )
        session.add_all([customer, line])
        session.add(
            PanelLine(
                line=line,
                platform=Platform.LIVE_HALLO,
                username=f"live-{name.lower()}",
                username_key=f"live-{name.lower()}",
                expires_on=live_date,
            )
        )
        session.add(
            PanelLine(
                line=line,
                platform=Platform.VOD_NEWVOD,
                username=f"vod-{name.lower()}",
                username_key=f"vod-{name.lower()}",
                expires_on=vod_date,
            )
        )

    add_line("Parziale", date(2025, 1, 1), date(2027, 1, 1))
    add_line("EntrambeScadute", date(2025, 1, 1), date(2025, 2, 1))
    add_line("EntrambeAttive", date(2027, 1, 1), date(2027, 1, 15))
    await session.flush()

    result = await link_import_reconcile(
        session,
        platforms=[Platform.LIVE_HALLO, Platform.VOD_NEWVOD],
        today=date(2026, 11, 1),
        import_missing=False,
    )

    assert len(result.reviews) == 1
    assert result.reviews[0].username == "live-parziale"
    assert "Live scaduto, VOD attivo" in result.reviews[0].reason


async def test_run_sync_dry_run_writes_nothing(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = FakeAdapter(
        Platform.LIVE_HALLO, [row(Platform.LIVE_HALLO, "mario", note="Mario Rossi")]
    )

    report = await run_sync(
        [adapter],
        today=date(2026, 11, 1),
        import_missing=True,
        dry_run=True,
        session_factory=factory,
    )

    assert report.dry_run is True
    assert report.results[0].imported == 1
    assert adapter.closed is True
    async with factory() as session:
        assert await session.scalar(select(func.count(PanelLine.id))) == 0
        assert await session.scalar(select(func.count(Line.id))) == 0
        assert await session.scalar(select(func.count(SyncRun.id))) == 0


async def test_run_sync_records_run(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = FakeAdapter(Platform.LIVE_HALLO, [row(Platform.LIVE_HALLO, "mario")])

    report = await run_sync(
        [adapter],
        today=date(2026, 11, 1),
        import_missing=True,
        session_factory=factory,
    )

    assert report.results[0].created == 1
    assert report.results[0].status is SyncStatus.SUCCESS
    async with factory() as session:
        runs = list((await session.scalars(select(SyncRun))).all())
        assert len(runs) == 1
        assert runs[0].platform is Platform.LIVE_HALLO
        assert runs[0].status is SyncStatus.SUCCESS
        assert runs[0].counts["fetched"] == 1


async def test_run_sync_reports_missing_without_deleting_line(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = FakeAdapter(Platform.LIVE_HALLO, [row(Platform.LIVE_HALLO, "mario")])
    await run_sync(
        [first],
        today=date(2026, 11, 1),
        import_missing=True,
        session_factory=factory,
    )

    second = FakeAdapter(Platform.LIVE_HALLO, [])
    report = await run_sync(
        [second],
        today=date(2026, 11, 2),
        session_factory=factory,
    )

    assert report.results[0].missing == 1
    assert report.results[0].status is SyncStatus.PARTIAL
    assert any("sparita dal pannello" in review.reason for review in report.results[0].review)
    async with factory() as session:
        assert await session.scalar(select(func.count(Line.id))) == 1
        assert await session.scalar(select(func.count(PanelLine.id))) == 1


async def test_sync_without_import_reports_unlinked(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = FakeAdapter(Platform.VOD_NEWVOD, [row(Platform.VOD_NEWVOD, "solo", note="Solo Vod")])

    report = await run_sync(
        [adapter],
        today=date(2026, 11, 1),
        session_factory=factory,
    )

    assert report.results[0].imported == 0
    assert report.results[0].status is SyncStatus.PARTIAL
    assert any("non collegata a una linea" in review.reason for review in report.results[0].review)


async def test_one_broken_panel_does_not_stop_the_others(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    broken = BrokenAdapter(Platform.LIVE_HALLO)
    eightplus = FakeAdapter(
        Platform.LIVE_8TVPLUS, [row(Platform.LIVE_8TVPLUS, "CODE1", note="Luca Verdi")]
    )

    report = await run_sync(
        [broken, eightplus],
        today=date(2026, 11, 1),
        import_missing=True,
        session_factory=factory,
    )

    assert len(report.failed) == 1
    assert report.failed[0].platform is Platform.LIVE_HALLO
    assert report.results[1].status is SyncStatus.SUCCESS
    async with factory() as session:
        runs = list((await session.scalars(select(SyncRun))).all())
        assert {run.platform for run in runs} == {Platform.LIVE_HALLO, Platform.LIVE_8TVPLUS}
        assert {run.status for run in runs} == {SyncStatus.FAILED, SyncStatus.SUCCESS}


async def test_disabled_line_is_not_reconciled(engine) -> None:
    """Una linea disattivata nel bot non riceve rinnovi né scadenze dal pannello."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = FakeAdapter(
        Platform.LIVE_HALLO, [row(Platform.LIVE_HALLO, "mario", expires_on=date(2026, 12, 15))]
    )
    await run_sync([first], today=date(2026, 11, 1), import_missing=True, session_factory=factory)

    from datetime import UTC, datetime

    async with factory() as session:
        line = await session.scalar(select(Line))
        line.deleted_at = datetime.now(UTC)
        await session.commit()
        line_id = line.id

    later = FakeAdapter(
        Platform.LIVE_HALLO, [row(Platform.LIVE_HALLO, "mario", expires_on=date(2027, 3, 15))]
    )
    report = await run_sync([later], today=date(2026, 11, 5), session_factory=factory)

    async with factory() as session:
        line = await session.get(Line, line_id, populate_existing=True)
        assert line.expires_on == date(2026, 12, 15)
        assert await session.scalar(select(func.count(Renewal.id))) == 0
    assert not any("rinnovo" in review.reason.lower() for review in report.results[0].review)


async def test_disabled_line_does_not_report_missing_row(engine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = FakeAdapter(Platform.LIVE_HALLO, [row(Platform.LIVE_HALLO, "mario")])
    await run_sync([first], today=date(2026, 11, 1), import_missing=True, session_factory=factory)

    from datetime import UTC, datetime

    async with factory() as session:
        line = await session.scalar(select(Line))
        line.deleted_at = datetime.now(UTC)
        await session.commit()

    empty = FakeAdapter(Platform.LIVE_HALLO, [])
    report = await run_sync([empty], today=date(2026, 11, 2), session_factory=factory)

    assert not any("sparita dal pannello" in review.reason for review in report.results[0].review)
