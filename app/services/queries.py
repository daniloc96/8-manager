from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import AuditLog, Customer, Line, Platform, Renewal, Reseller
from app.services.months import add_months
from app.services.text import normalize_key


@dataclass(slots=True)
class DueGroups:
    """Scadenze raggruppate come le mostra il menu."""

    expired: list[Line] = field(default_factory=list)
    today: list[Line] = field(default_factory=list)
    soon: list[Line] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.expired) + len(self.today) + len(self.soon)


@dataclass(slots=True)
class IncomeRow:
    reseller: str | None
    renewals: int
    total_cents: int


@dataclass(slots=True)
class IncomeReport:
    year: int
    month: int
    rows: list[IncomeRow] = field(default_factory=list)
    total_cents: int = 0
    # Valore del parco: linee attive e somma dei prezzi mensili. Non dipende
    # dai rinnovi del mese, serve solo a leggere la dimensione del giro.
    active_lines: int = 0
    monthly_cents: int = 0
    # Clienti distinti dietro quelle linee: alcuni hanno Live e VOD, o due
    # linee, ma il guadagno non si conta due volte per lo stesso cliente.
    active_customers: int = 0


async def active_lines(session: AsyncSession) -> list[Line]:
    return list((await session.scalars(select(Line).where(Line.deleted_at.is_(None)))).all())


def _sort_key(line: Line) -> tuple[date, str, str]:
    return (line.expires_on, normalize_key(line.customer.name), normalize_key(line.label))


async def due_groups(
    session: AsyncSession,
    today: date,
    *,
    warn_days: int = 3,
    reseller_id: int | None = None,
) -> DueGroups:
    groups = DueGroups()
    threshold = today.toordinal() + warn_days
    muted = await muted_line_ids(session, today)

    for line in await active_lines(session):
        if line.id in muted:
            # anomalia accettata: non è rumore da scadenze finché dura
            continue
        if reseller_id is not None and line.reseller_id != reseller_id:
            continue
        if line.expires_on < today:
            groups.expired.append(line)
        elif line.expires_on == today:
            groups.today.append(line)
        elif line.expires_on.toordinal() <= threshold:
            groups.soon.append(line)

    for group in (groups.expired, groups.today, groups.soon):
        group.sort(key=_sort_key)
    groups.expired.reverse()
    return groups


def _text_key(line: Line) -> str:
    return normalize_key(
        " ".join([line.customer.name, line.customer.alias or "", line.label or ""])
    )


def _code_key(line: Line) -> str:
    parts = [str(line.id)]
    for panel in line.panel_lines:
        parts.extend([panel.username, panel.external_id or "", panel.access_code or ""])
    return normalize_key(" ".join(parts))


async def search_lines(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 12,
    today: date | None = None,
    reseller_id: int | None = None,
) -> list[Line]:
    """Ricerca libera su nome, alias, etichetta, username e ID.

    Il dataset è piccolo (decine di linee): filtrare in Python permette di
    cercare anche dentro username e codici senza costruire indici dedicati.
    """
    tokens = normalize_key(query).split()
    if not tokens:
        return []

    matches: list[Line] = []
    for line in await active_lines(session):
        if reseller_id is not None and line.reseller_id != reseller_id:
            continue
        text_key = _text_key(line)
        code_key = _code_key(line)
        # I token corti (spesso numeri) cercano solo nei testi: "2" non deve
        # pescare tutte le linee il cui username contiene una cifra 2.
        if all(token in text_key or (len(token) >= 4 and token in code_key) for token in tokens):
            matches.append(line)
    reference = today or today_of()
    matches.sort(key=lambda line: (line.expires_on < reference, _sort_key(line)))
    return matches[:limit]


@dataclass(slots=True)
class CustomerOverview:
    """Un cliente con tutte le sue linee attive e il totale mensile."""

    customer: Customer
    lines: list[Line] = field(default_factory=list)
    monthly_cents: int = 0
    hidden_lines: int = 0

    @property
    def active_lines(self) -> int:
        return len(self.lines)


async def customer_overview(
    session: AsyncSession,
    customer_id: int,
    *,
    reseller_id: int | None = None,
) -> CustomerOverview | None:
    """Scheda cliente. Con `reseller_id` conta solo le linee di quel rivenditore."""
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        return None

    rows = list(
        (
            await session.scalars(
                select(Line).where(Line.customer_id == customer_id, Line.deleted_at.is_(None))
            )
        ).all()
    )
    rows.sort(key=_sort_key)
    visible = [line for line in rows if reseller_id is None or line.reseller_id == reseller_id]
    return CustomerOverview(
        customer=customer,
        lines=visible,
        monthly_cents=sum(line.monthly_price_cents for line in visible),
        hidden_lines=len(rows) - len(visible),
    )


async def get_line(session: AsyncSession, line_id: int) -> Line | None:
    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        return None
    return line


async def restore_line(session: AsyncSession, line_id: int) -> Line | None:
    """Riattiva una linea disattivata nel bot (soft delete annullato)."""
    from app.models import Line

    line = await session.get(Line, line_id)
    if line is None:
        return None
    line.deleted_at = None
    session.add(
        AuditLog(
            action="line_restored",
            entity="line",
            entity_id=line.id,
            payload={},
        )
    )
    await session.flush()
    return line


async def get_customer(session: AsyncSession, customer_id: int) -> Customer | None:
    return await session.get(Customer, customer_id)


def panel_for(line: Line, platform: Platform):
    for panel in line.panel_lines:
        if panel.platform == platform:
            return panel
    return None


async def income_report(session: AsyncSession, year: int, month: int) -> IncomeReport:
    """Incassi registrati nel mese: rinnovi creati, raggruppati per rivenditore."""
    start = date(year, month, 1)
    end = add_months(start, 1, 1)
    start_at = datetime(start.year, start.month, start.day, tzinfo=UTC)
    end_at = datetime(end.year, end.month, end.day, tzinfo=UTC)

    renewals = list(
        (
            await session.scalars(
                select(Renewal)
                .options(joinedload(Renewal.line).joinedload(Line.reseller))
                .where(Renewal.created_at >= start_at, Renewal.created_at < end_at)
            )
        ).all()
    )

    buckets: dict[str | None, IncomeRow] = {}
    report = IncomeReport(year=year, month=month)
    for renewal in renewals:
        line = renewal.line
        reseller = line.reseller.name if line.reseller is not None else None
        row = buckets.get(reseller)
        if row is None:
            row = IncomeRow(reseller=reseller, renewals=0, total_cents=0)
            buckets[reseller] = row
        row.renewals += 1
        row.total_cents += renewal.total_cents
        report.total_cents += renewal.total_cents

    report.rows = sorted(
        buckets.values(), key=lambda item: (item.reseller is None, item.reseller or "")
    )

    portfolio = await active_lines(session)
    report.active_lines = len(portfolio)
    report.active_customers = len({line.customer_id for line in portfolio})
    report.monthly_cents = sum(line.monthly_price_cents for line in portfolio)
    return report


async def muted_line_ids(session: AsyncSession, today: date) -> set[int]:
    """Linee con un'eccezione attiva: le anomalie accettate non fanno rumore."""
    from app.services.review_exceptions import is_active, load_exceptions

    return {
        line_id
        for line_id, record in (await load_exceptions(session)).items()
        if is_active(record, today)
    }


def today_of() -> date:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.config import get_settings

    return datetime.now(ZoneInfo(get_settings().tz)).date()


async def reseller_by_telegram_id(session: AsyncSession, telegram_user_id: int) -> Reseller | None:
    return await session.scalar(
        select(Reseller).where(
            Reseller.telegram_user_id == telegram_user_id,
            Reseller.active.is_(True),
        )
    )


async def lines_of_reseller(session: AsyncSession, reseller_id: int) -> list[Line]:
    lines = [line for line in await active_lines(session) if line.reseller_id == reseller_id]
    lines.sort(key=_sort_key)
    return lines
