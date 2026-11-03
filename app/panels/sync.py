from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import get_session_factory
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
    utcnow,
)
from app.panels.base import PanelAdapter
from app.panels.base import PanelLine as PanelRow
from app.services.months import months_between
from app.services.text import customer_search_key, normalize_key

logger = logging.getLogger(__name__)

LUCKY_PLATFORMS = (Platform.LIVE_HALLO, Platform.VOD_NEWVOD)
IMPORT_NOTE = "Importato dal pannello: prezzo da impostare"


class SyncIssue(StrEnum):
    """Anomalie che l'owner può gestire da solo, senza toccare i pannelli."""

    EXPIRY_DIFF = "expiry_diff"  # data locale più avanti di quella del pannello
    LOCAL_AHEAD = "local_ahead"


@dataclass(slots=True)
class ReviewItem:
    platform: Platform
    username: str
    reason: str
    line_id: int | None = None
    issue: str | None = None
    local_expires: date | None = None
    panel_expires: date | None = None


@dataclass(slots=True)
class PlatformResult:
    platform: Platform
    status: SyncStatus = SyncStatus.SUCCESS
    fetched: int = 0
    created: int = 0
    updated: int = 0
    linked: int = 0
    imported: int = 0
    missing: int = 0
    expiry_updates: int = 0
    renewals: int = 0
    historical: int = 0
    error: str | None = None
    review: list[ReviewItem] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "linked": self.linked,
            "imported": self.imported,
            "missing": self.missing,
            "expiry_updates": self.expiry_updates,
            "renewals": self.renewals,
            "historical": self.historical,
            "review": len(self.review),
        }


@dataclass(slots=True)
class SyncReport:
    started_at: datetime
    finished_at: datetime | None = None
    dry_run: bool = False
    import_missing: bool = False
    suppressed: int = 0
    due_exceptions: list[tuple[int, str, str]] = field(default_factory=list)
    results: list[PlatformResult] = field(default_factory=list)
    skipped: list[tuple[Platform, str]] = field(default_factory=list)

    @property
    def failed(self) -> list[PlatformResult]:
        return [result for result in self.results if result.status is SyncStatus.FAILED]

    @property
    def review_count(self) -> int:
        return sum(len(result.review) for result in self.results)


@dataclass(slots=True)
class UpsertResult:
    platform: Platform
    created: int = 0
    updated: int = 0
    missing: list[PanelLine] = field(default_factory=list)
    skipped: list[tuple[PanelRow, str]] = field(default_factory=list)


@dataclass(slots=True)
class LinkResult:
    linked: dict[Platform, int] = field(default_factory=dict)
    imported: dict[Platform, int] = field(default_factory=dict)
    expiry_updates: dict[Platform, int] = field(default_factory=dict)
    renewals: dict[Platform, int] = field(default_factory=dict)
    historical: dict[Platform, int] = field(default_factory=dict)
    reviews: list[ReviewItem] = field(default_factory=list)


async def upsert_rows(
    session: AsyncSession, platform: Platform, rows: Sequence[PanelRow]
) -> UpsertResult:
    """Scrive la fotografia del pannello senza toccare i collegamenti esistenti."""
    result = UpsertResult(platform=platform)
    existing = list(
        (await session.scalars(select(PanelLine).where(PanelLine.platform == platform))).all()
    )
    by_key = {record.username_key: record for record in existing}

    seen: set[str] = set()
    now = utcnow()
    for row in rows:
        key = row.username_key
        if not key:
            result.skipped.append((row, "username mancante"))
            continue
        if key in seen:
            result.skipped.append((row, "username duplicato nel pannello"))
            continue
        seen.add(key)

        record = by_key.get(key)
        if record is None:
            record = PanelLine(
                platform=platform,
                username=row.username,
                username_key=key,
                first_seen_at=now,
            )
            session.add(record)
            by_key[key] = record
            result.created += 1
        else:
            result.updated += 1

        record.username = row.username
        record.last_seen_at = now
        record.raw_json = row.raw
        if row.external_id:
            record.external_id = row.external_id
        if row.password is not None:
            record.password = row.password
        if row.access_code is not None:
            record.access_code = row.access_code
        if row.expires_on is not None:
            record.expires_on = row.expires_on
        if row.status is not None:
            record.status = row.status
        if row.note is not None:
            record.note = row.note
        if row.package is not None:
            record.package = row.package

    await session.flush()

    result.missing = [record for record in existing if record.username_key not in seen]
    return result


async def link_import_reconcile(
    session: AsyncSession,
    *,
    platforms: Sequence[Platform],
    today: date,
    import_missing: bool,
) -> LinkResult:
    """Collega Live e VOD, importa le righe nuove e allinea le scadenze."""
    result = LinkResult()
    affected = _affected_platforms(platforms)
    records = list(
        (await session.scalars(select(PanelLine).where(PanelLine.platform.in_(affected)))).all()
    )
    groups = _group_records(records)

    line_ids = {record.line_id for record in records if record.line_id is not None}
    lines: dict[int, Line] = {}
    if line_ids:
        lines = {
            line.id: line
            for line in (await session.scalars(select(Line).where(Line.id.in_(line_ids)))).all()
        }

    occupied = {
        (record.line_id, record.platform) for record in records if record.line_id is not None
    }
    platforms_by_line: dict[int, set[Platform]] = {}
    for record in records:
        if record.line_id is not None:
            platforms_by_line.setdefault(record.line_id, set()).add(record.platform)

    _link_lucky_pairs(result, groups, occupied, platforms_by_line, today)
    _upgrade_lucky_services(lines, platforms_by_line)

    if import_missing:
        await _import_groups(
            session,
            result=result,
            groups=groups,
            platforms=set(platforms),
            today=today,
            lines=lines,
            occupied=occupied,
            platforms_by_line=platforms_by_line,
        )

    _review_unlinked(result, records, set(platforms), today)
    await _reconcile_expiries(session, result=result, records=records, lines=lines, today=today)
    await session.flush()
    return result


def _link_lucky_pairs(
    result: LinkResult,
    groups: dict[tuple[str, str], dict[Platform, PanelLine]],
    occupied: set[tuple[int | None, Platform]],
    platforms_by_line: dict[int, set[Platform]],
    today: date,
) -> None:
    for key in sorted(groups):
        family, _username_key = key
        if family != "lucky":
            continue
        by_platform = groups[key]
        live = by_platform.get(Platform.LIVE_HALLO)
        vod = by_platform.get(Platform.VOD_NEWVOD)
        if live is None or vod is None:
            continue

        if live.line_id is not None and vod.line_id is not None:
            if live.line_id != vod.line_id and not _is_expired(vod, today):
                _add_review(
                    result, Platform.VOD_NEWVOD, vod.username, "Live e VOD su linee diverse"
                )
            continue

        if live.line_id is not None:
            _link_counterpart(
                result,
                source=live,
                target=vod,
                occupied=occupied,
                platforms_by_line=platforms_by_line,
                today=today,
            )
        elif vod.line_id is not None:
            _link_counterpart(
                result,
                source=vod,
                target=live,
                occupied=occupied,
                platforms_by_line=platforms_by_line,
                today=today,
            )


def _link_counterpart(
    result: LinkResult,
    *,
    source: PanelLine,
    target: PanelLine,
    occupied: set[tuple[int | None, Platform]],
    platforms_by_line: dict[int, set[Platform]],
    today: date,
) -> None:
    assert source.line_id is not None
    target_expired = _is_expired(target, today)
    ok, warning = _notes_state(source.note, target.note)
    if not ok:
        if not target_expired:
            _add_review(
                result,
                target.platform,
                target.username,
                "note diverse tra Live e VOD",
            )
        return
    if warning and not target_expired:
        _add_review(result, target.platform, target.username, warning)

    if (source.line_id, target.platform) in occupied:
        if not target_expired:
            _add_review(
                result,
                target.platform,
                target.username,
                f"linea già collegata a un'altra riga {target.platform.value}",
            )
        return

    target.line_id = source.line_id
    occupied.add((source.line_id, target.platform))
    platforms_by_line.setdefault(source.line_id, set()).add(target.platform)
    _bump(result.linked, target.platform)


def _upgrade_lucky_services(
    lines: dict[int, Line], platforms_by_line: dict[int, set[Platform]]
) -> None:
    for line_id, platforms in platforms_by_line.items():
        line = lines.get(line_id)
        if line is None:
            continue
        if (
            Platform.LIVE_HALLO in platforms
            and Platform.VOD_NEWVOD in platforms
            and line.service != Service.LIVE_VOD
        ):
            line.service = Service.LIVE_VOD


async def _import_groups(
    session: AsyncSession,
    *,
    result: LinkResult,
    groups: dict[tuple[str, str], dict[Platform, PanelLine]],
    platforms: set[Platform],
    today: date,
    lines: dict[int, Line],
    occupied: set[tuple[int | None, Platform]],
    platforms_by_line: dict[int, set[Platform]],
) -> None:
    customer_index = await _load_customer_index(session)
    touched_customers: set[int] = set()

    for key in sorted(groups):
        family, _username_key = key
        by_platform = groups[key]
        if not any(record.platform in platforms for record in by_platform.values()):
            continue
        if any(record.line_id is not None for record in by_platform.values()):
            continue

        selected, service, name = _import_candidate(result, family, by_platform, today)
        if not selected:
            continue
        if not name or not name.strip():
            _add_review(result, selected[0].platform, selected[0].username, "nome cliente mancante")
            continue

        dates = [record.expires_on for record in selected if record.expires_on is not None]
        if not dates:
            _add_review(
                result, selected[0].platform, selected[0].username, "scadenza pannello mancante"
            )
            continue

        customer = await _resolve_customer(session, name, customer_index)
        if customer is None:
            _add_review(
                result,
                selected[0].platform,
                selected[0].username,
                "più clienti con lo stesso nome",
            )
            continue

        expires_on = min(dates)
        line = Line(
            customer=customer,
            service=service,
            monthly_price_cents=0,
            started_on=min(today, expires_on),
            expires_on=expires_on,
            anchor_day=expires_on.day,
            notes=IMPORT_NOTE,
        )
        session.add(line)
        await session.flush()

        linked_here: list[Platform] = []
        for record in selected:
            if (line.id, record.platform) in occupied:
                _add_review(
                    result,
                    record.platform,
                    record.username,
                    f"linea già collegata a un'altra riga {record.platform.value}",
                )
                continue
            record.line_id = line.id
            occupied.add((line.id, record.platform))
            platforms_by_line.setdefault(line.id, set()).add(record.platform)
            _bump(result.imported, record.platform)
            linked_here.append(record.platform)

        if not linked_here:
            continue

        lines[line.id] = line
        touched_customers.add(customer.id)
        session.add(
            AuditLog(
                action="panel_import",
                entity="line",
                entity_id=line.id,
                payload={
                    "platforms": sorted(platform.value for platform in linked_here),
                    "service": service.value,
                    "expires_on": expires_on.isoformat(),
                },
            )
        )

    for customer_id in sorted(touched_customers):
        await _assign_labels(session, customer_id)


def _import_candidate(
    result: LinkResult,
    family: str,
    by_platform: dict[Platform, PanelLine],
    today: date,
) -> tuple[list[PanelLine], Service, str | None]:
    if family == "8plus":
        row = by_platform.get(Platform.LIVE_8TVPLUS)
        if row is None:
            return [], Service.LIVE, None
        return [row], Service.LIVE, row.note

    live = by_platform.get(Platform.LIVE_HALLO)
    vod = by_platform.get(Platform.VOD_NEWVOD)

    if live is not None and vod is not None:
        vod_expired = _is_expired(vod, today)
        ok, warning = _notes_state(live.note, vod.note)
        if ok and warning and not vod_expired:
            _add_review(result, Platform.VOD_NEWVOD, vod.username, warning)
        if not ok:
            if not vod_expired:
                _add_review(
                    result,
                    Platform.VOD_NEWVOD,
                    vod.username,
                    "note diverse tra Live e VOD: VOD lasciato da rivedere",
                )
            return [live], Service.LIVE, live.note or vod.note
        return [live, vod], Service.LIVE_VOD, live.note or vod.note

    if live is not None:
        return [live], Service.LIVE, live.note

    if vod is not None and not _is_expired(vod, today):
        _add_review(result, Platform.VOD_NEWVOD, vod.username, "VOD senza Live")
    return [], Service.LIVE, None


def _review_unlinked(
    result: LinkResult,
    records: Sequence[PanelLine],
    platforms: set[Platform],
    today: date,
) -> None:
    reviewed = {(review.platform, review.username) for review in result.reviews}
    for record in records:
        if record.platform not in platforms or record.line_id is not None:
            continue
        if (record.platform, record.username) in reviewed:
            continue
        if _is_expired(record, today):
            _bump(result.historical, record.platform)
            continue
        _add_review(
            result,
            record.platform,
            record.username,
            "riga pannello non collegata a una linea",
        )


async def _reconcile_expiries(
    session: AsyncSession,
    *,
    result: LinkResult,
    records: Sequence[PanelLine],
    lines: dict[int, Line],
    today: date,
) -> None:
    by_line: dict[int, dict[Platform, PanelLine]] = {}
    for record in records:
        if record.line_id is not None:
            by_line.setdefault(record.line_id, {})[record.platform] = record

    for line_id in sorted(by_line):
        line = lines.get(line_id)
        if line is None:
            line = await session.get(Line, line_id)
            if line is None:
                continue
            lines[line_id] = line
        if line.deleted_at is not None:
            # linea disattivata nel bot: non si riconcilia e non si rinnova
            continue

        rows = by_line[line_id]
        lucky_dates = {
            platform: rows[platform].expires_on
            for platform in LUCKY_PLATFORMS
            if platform in rows and rows[platform].expires_on is not None
        }
        eightplus = rows.get(Platform.LIVE_8TVPLUS)

        if eightplus is not None and eightplus.expires_on is not None:
            effective = eightplus.expires_on
            primary = Platform.LIVE_8TVPLUS
            username = eightplus.username
        elif lucky_dates:
            effective = min(lucky_dates.values())
            primary = (
                Platform.LIVE_HALLO if Platform.LIVE_HALLO in lucky_dates else Platform.VOD_NEWVOD
            )
            username = rows[primary].username
        else:
            primary_row = next(iter(rows.values()))
            _add_review(
                result,
                primary_row.platform,
                primary_row.username,
                "scadenza pannello mancante",
            )
            continue

        live_date = lucky_dates.get(Platform.LIVE_HALLO)
        vod_date = lucky_dates.get(Platform.VOD_NEWVOD)
        if live_date is not None and vod_date is not None and live_date != vod_date:
            live_active = live_date >= today
            vod_active = vod_date >= today
            if live_active != vod_active:
                if not live_active:
                    _add_review(
                        result,
                        Platform.LIVE_HALLO,
                        rows[Platform.LIVE_HALLO].username,
                        f"Live scaduto, VOD attivo fino al {vod_date:%d/%m/%Y}",
                    )
                else:
                    _add_review(
                        result,
                        Platform.VOD_NEWVOD,
                        rows[Platform.VOD_NEWVOD].username,
                        f"VOD scaduto, Live attivo fino al {live_date:%d/%m/%Y}",
                    )

        if line.expires_on == effective:
            continue

        previous = line.expires_on
        line.expires_on = effective
        _bump(result.expiry_updates, primary)
        session.add(
            AuditLog(
                action="panel_expiry",
                entity="line",
                entity_id=line.id,
                payload={
                    "old": previous.isoformat(),
                    "new": effective.isoformat(),
                    "platform": primary.value,
                },
            )
        )

        if effective > previous:
            months = months_between(previous, effective, line.anchor_day)
            if months is not None and months > 0:
                session.add(
                    Renewal(
                        line_id=line.id,
                        months=months,
                        monthly_price_cents=line.monthly_price_cents,
                        total_cents=line.monthly_price_cents * months,
                        period_start=previous,
                        period_end=effective,
                        source=RenewalSource.SYNC,
                        note="Rinnovo rilevato dal sync pannelli",
                    )
                )
                _bump(result.renewals, primary)
            else:
                _add_review(
                    result,
                    primary,
                    username,
                    "salto di scadenza non a mesi interi: rinnovo da registrare a mano",
                )
        elif effective < previous:
            warning = (
                f"data locale {previous:%d/%m/%Y} più avanti del pannello ({effective:%d/%m/%Y})"
            )
            _add_review(
                result,
                primary,
                username,
                warning,
                line_id=line.id,
                issue=SyncIssue.LOCAL_AHEAD.value,
                local_expires=previous,
                panel_expires=effective,
            )


async def run_sync(
    adapters: Sequence[PanelAdapter],
    *,
    today: date | None = None,
    import_missing: bool = False,
    dry_run: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    skipped: Sequence[tuple[Platform, str]] = (),
) -> SyncReport:
    """Legge i pannelli, scrive la fotografia e riconcilia le linee."""
    settings = get_settings()
    today = today or datetime.now(ZoneInfo(settings.tz)).date()
    factory = session_factory or get_session_factory()
    report = SyncReport(
        started_at=utcnow(),
        dry_run=dry_run,
        import_missing=import_missing,
        skipped=list(skipped),
    )
    results = {adapter.platform: PlatformResult(platform=adapter.platform) for adapter in adapters}

    fetched: list[tuple[PanelAdapter, list[PanelRow] | None, str | None]] = []
    for adapter in adapters:
        try:
            rows = await adapter.fetch_lines()
            fetched.append((adapter, rows, None))
        except Exception as exc:  # un pannello rotto non deve fermare gli altri
            logger.exception("Sync %s fallito", adapter.platform.value)
            fetched.append((adapter, None, _short_error(exc)))
        finally:
            try:
                await adapter.aclose()
            except Exception:
                logger.warning("%s: chiusura client fallita", adapter.platform.value, exc_info=True)

    try:
        async with factory() as session:
            deleted_ids = set(
                (await session.scalars(select(Line.id).where(Line.deleted_at.is_not(None)))).all()
            )
            for adapter, rows, error in fetched:
                result = results[adapter.platform]
                if rows is None:
                    result.status = SyncStatus.FAILED
                    result.error = error
                    continue

                result.fetched = len(rows)
                upsert = await upsert_rows(session, adapter.platform, rows)
                result.created = upsert.created
                result.updated = upsert.updated
                result.missing = len(upsert.missing)
                for row, reason in upsert.skipped:
                    result.review.append(
                        ReviewItem(platform=adapter.platform, username=row.username, reason=reason)
                    )
                for record in upsert.missing:
                    if record.line_id is not None and record.line_id not in deleted_ids:
                        result.review.append(
                            ReviewItem(
                                platform=adapter.platform,
                                username=record.username,
                                reason="riga sparita dal pannello",
                            )
                        )

            synced = [adapter.platform for adapter, rows, _error in fetched if rows is not None]
            if synced:
                link = await link_import_reconcile(
                    session,
                    platforms=synced,
                    today=today,
                    import_missing=import_missing,
                )
                for platform, count in link.linked.items():
                    if platform in results:
                        results[platform].linked = count
                for platform, count in link.imported.items():
                    if platform in results:
                        results[platform].imported = count
                for platform, count in link.expiry_updates.items():
                    if platform in results:
                        results[platform].expiry_updates = count
                for platform, count in link.renewals.items():
                    if platform in results:
                        results[platform].renewals = count
                for platform, count in link.historical.items():
                    if platform in results:
                        results[platform].historical = count
                for review in link.reviews:
                    if review.platform in results:
                        results[review.platform].review.append(review)

                report.suppressed, report.due_exceptions = await _apply_exceptions(
                    session, results, today
                )

            for result in results.values():
                if result.status is not SyncStatus.FAILED:
                    result.status = SyncStatus.PARTIAL if result.review else SyncStatus.SUCCESS

            if not dry_run:
                for adapter, _rows, _error in fetched:
                    result = results[adapter.platform]
                    session.add(
                        SyncRun(
                            platform=adapter.platform,
                            started_at=report.started_at,
                            finished_at=utcnow(),
                            status=result.status,
                            counts=result.counts(),
                            error=result.error,
                        )
                    )

            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    except Exception as exc:
        logger.exception("Sync interrotto")
        for result in results.values():
            if result.status is not SyncStatus.FAILED:
                result.status = SyncStatus.FAILED
                result.error = _short_error(exc)
        if not dry_run:
            await _record_failed_runs(factory, results, report.started_at)

    report.finished_at = utcnow()
    report.results = list(results.values())
    return report


async def _record_failed_runs(
    factory: async_sessionmaker[AsyncSession],
    results: dict[Platform, PlatformResult],
    started_at: datetime,
) -> None:
    try:
        async with factory() as session:
            for result in results.values():
                if result.status is not SyncStatus.FAILED:
                    continue
                session.add(
                    SyncRun(
                        platform=result.platform,
                        started_at=started_at,
                        finished_at=utcnow(),
                        status=SyncStatus.FAILED,
                        counts=result.counts(),
                        error=result.error,
                    )
                )
            await session.commit()
    except Exception:
        logger.exception("Impossibile registrare i sync falliti")


async def _load_customer_index(session: AsyncSession) -> dict[str, list[Customer]]:
    customers = (await session.scalars(select(Customer).where(Customer.deleted_at.is_(None)))).all()
    index: dict[str, list[Customer]] = {}
    for customer in customers:
        index.setdefault(normalize_key(customer.name), []).append(customer)
    return index


async def _resolve_customer(
    session: AsyncSession,
    name: str,
    index: dict[str, list[Customer]],
) -> Customer | None:
    key = normalize_key(name)
    matches = index.get(key)
    if matches:
        if len(matches) > 1:
            return None
        return matches[0]

    customer = Customer(name=name.strip(), search_key=customer_search_key(name))
    session.add(customer)
    await session.flush()
    index[key] = [customer]
    return customer


async def _assign_labels(session: AsyncSession, customer_id: int) -> None:
    lines = list(
        (
            await session.scalars(
                select(Line)
                .where(Line.customer_id == customer_id, Line.deleted_at.is_(None))
                .order_by(Line.id)
            )
        ).all()
    )
    if len(lines) <= 1:
        return

    used = {line.label for line in lines if line.label}
    number = 1
    for line in lines:
        if line.label:
            continue
        while f"Linea {number}" in used:
            number += 1
        line.label = f"Linea {number}"
        used.add(line.label)


def _group_records(
    records: Sequence[PanelLine],
) -> dict[tuple[str, str], dict[Platform, PanelLine]]:
    groups: dict[tuple[str, str], dict[Platform, PanelLine]] = {}
    for record in records:
        groups.setdefault((_family(record.platform), record.username_key), {})[record.platform] = (
            record
        )
    return groups


def _family(platform: Platform) -> str:
    return "lucky" if platform in LUCKY_PLATFORMS else "8plus"


def _affected_platforms(platforms: Sequence[Platform]) -> tuple[Platform, ...]:
    affected: set[Platform] = set()
    if Platform.LIVE_HALLO in platforms or Platform.VOD_NEWVOD in platforms:
        affected.update(LUCKY_PLATFORMS)
    if Platform.LIVE_8TVPLUS in platforms:
        affected.add(Platform.LIVE_8TVPLUS)
    return tuple(affected)


def _is_expired(record: PanelLine, today: date) -> bool:
    return record.expires_on is not None and record.expires_on < today


def _notes_state(left: str | None, right: str | None) -> tuple[bool, str | None]:
    left_key = normalize_key(left)
    right_key = normalize_key(right)
    if not left_key and not right_key:
        return False, "note mancanti su Live e VOD"
    if not left_key or not right_key:
        return True, "nota mancante su un pannello"
    if left_key == right_key:
        return True, None

    left_tokens = set(left_key.split())
    right_tokens = set(right_key.split())
    if (
        len(left_tokens) >= 2
        and len(right_tokens) >= 2
        and (left_tokens <= right_tokens or right_tokens <= left_tokens)
    ):
        return True, None
    return False, "note diverse tra Live e VOD"


def _add_review(
    result: LinkResult,
    platform: Platform,
    username: str,
    reason: str,
    *,
    line_id: int | None = None,
    issue: str | None = None,
    local_expires: date | None = None,
    panel_expires: date | None = None,
) -> None:
    result.reviews.append(
        ReviewItem(
            platform=platform,
            username=username,
            reason=reason,
            line_id=line_id,
            issue=issue,
            local_expires=local_expires,
            panel_expires=panel_expires,
        )
    )


async def _apply_exceptions(
    session: AsyncSession,
    results: dict[Platform, PlatformResult],
    today: date,
) -> tuple[int, list[tuple[int, str, str]]]:
    """Toglie dalle review le anomalie accettate e riporta quelle da rivedere."""
    from app.models import ReviewException
    from app.services.review_exceptions import is_active, is_due

    records = (await session.scalars(select(ReviewException))).all()
    if not records:
        return 0, []

    by_line = {record.line_id: record for record in records}
    rows = (
        await session.execute(
            select(PanelLine.platform, PanelLine.username_key, PanelLine.line_id).where(
                PanelLine.line_id.in_(by_line)
            )
        )
    ).all()
    line_of = {(platform, key): line_id for platform, key, line_id in rows}

    suppressed = 0
    for result in results.values():
        kept: list[ReviewItem] = []
        for review in result.review:
            line_id = line_of.get((review.platform, normalize_key(review.username)))
            record = by_line.get(line_id) if line_id is not None else None
            if record is not None and is_active(record, today):
                suppressed += 1
                continue
            kept.append(review)
        result.review = kept

    due = [
        (record.line_id, record.reason, record.review_on.isoformat())
        for record in sorted(by_line.values(), key=lambda item: item.line_id)
        if is_due(record, today) and record.review_on is not None
    ]
    return suppressed, due


def _bump(counter: dict[Platform, int], platform: Platform) -> None:
    counter[platform] = counter.get(platform, 0) + 1


def _short_error(exc: BaseException) -> str:
    message = str(exc).strip()
    return (message or type(exc).__name__)[:300]
