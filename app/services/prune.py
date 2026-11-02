from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Line, PanelLine, Platform, SyncRun, SyncStatus, utcnow

# Oltre questa quota di righe sparite il comando si ferma: un pannello che
# risponde con una lista vuota non deve chiudere mezzo archivio.
MAX_MISSING_RATIO = 0.30


class PruneBlocked(RuntimeError):
    """Troppe righe sparite: serve --force per procedere."""

    def __init__(self, ratios: dict[Platform, float]) -> None:
        self.ratios = ratios
        super().__init__("troppe righe sparite rispetto al totale")


@dataclass(slots=True)
class PruneReport:
    missing_by_platform: dict[Platform, int] = field(default_factory=dict)
    total_by_platform: dict[Platform, int] = field(default_factory=dict)
    closed: list[tuple[int, str, date]] = field(default_factory=list)
    to_close: list[tuple[int, str, date]] = field(default_factory=list)
    undecided: list[tuple[int, str, date, list[str], str]] = field(default_factory=list)
    orphan_rows: int = 0
    applied: bool = False

    def ratios(self) -> dict[Platform, float]:
        return {
            platform: missing / total
            for platform, missing in self.missing_by_platform.items()
            if (total := self.total_by_platform.get(platform))
        }

    def over_limit(self) -> list[Platform]:
        return [p for p, ratio in self.ratios().items() if ratio > MAX_MISSING_RATIO]


async def _missing_thresholds(session: AsyncSession) -> dict[Platform, datetime]:
    """Inizio dell'ultima sync riuscita per ogni pannello.

    Una riga con `last_seen_at` precedente a quella soglia non è più sul
    pannello: la sync l'ha trovata «sparita».
    """
    rows = await session.execute(
        select(SyncRun.platform, func.max(SyncRun.started_at))
        .where(SyncRun.status.in_([SyncStatus.SUCCESS, SyncStatus.PARTIAL]))
        .group_by(SyncRun.platform)
    )
    return {platform: started for platform, started in rows.all() if started is not None}


def _naive(value: datetime) -> datetime:
    """SQLite restituisce datetime senza fuso: normalizzo prima di confrontare."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


async def prune_missing(
    session: AsyncSession,
    *,
    today: date,
    apply: bool = False,
    force: bool = False,
) -> PruneReport:
    """Chiude le linee sparite da tutti i pannelli e cancella le righe orfane."""
    report = PruneReport(applied=apply)
    thresholds = await _missing_thresholds(session)

    records = (await session.scalars(select(PanelLine))).all()
    missing_ids: set[int] = set()
    for record in records:
        report.total_by_platform[record.platform] = (
            report.total_by_platform.get(record.platform, 0) + 1
        )
        threshold = thresholds.get(record.platform)
        if threshold is not None and _naive(record.last_seen_at) < _naive(threshold):
            missing_ids.add(record.id)
            report.missing_by_platform[record.platform] = (
                report.missing_by_platform.get(record.platform, 0) + 1
            )

    by_line: dict[int, list[PanelLine]] = {}
    orphan_missing: list[PanelLine] = []
    for record in records:
        if record.id not in missing_ids:
            continue
        if record.line_id is None:
            orphan_missing.append(record)
        else:
            by_line.setdefault(record.line_id, []).append(record)
    report.orphan_rows = len(orphan_missing)

    if apply and not force:
        over = report.over_limit()
        if over:
            raise PruneBlocked({platform: report.ratios()[platform] for platform in over})

    lines = {
        line.id: line
        for line in (
            await session.scalars(
                select(Line).where(Line.id.in_(by_line), Line.deleted_at.is_(None))
            )
        ).all()
    }

    # Una linea si chiude quando sono sparite tutte le sue righe dai pannelli:
    # la cancellazione remota è la fonte di verità. Se invece una riga è
    # ancora presente, la linea resta e va decisa a mano.
    closable: list[tuple[Line, list[PanelLine]]] = []
    for line_id, gone in sorted(by_line.items()):
        line = lines.get(line_id)
        if line is None:
            continue
        still_there = [
            record
            for record in records
            if record.line_id == line_id and record.id not in missing_ids
        ]
        if still_there:
            report.undecided.append(
                (
                    line.id,
                    line.customer.name,
                    line.expires_on,
                    [record.username for record in gone],
                    "sparita solo da un pannello",
                )
            )
            continue
        closable.append((line, gone))

    report.to_close = [(line.id, line.customer.name, line.expires_on) for line, _ in closable]

    if not apply:
        return report

    now = utcnow()
    for line, gone in closable:
        line.deleted_at = now
        session.add(
            AuditLog(
                action="line_closed",
                entity="line",
                entity_id=line.id,
                payload={
                    "reason": "righe sparite dai pannelli",
                    "expires_on": line.expires_on.isoformat(),
                    "panel_lines": [record.username for record in gone],
                },
            )
        )
        report.closed.append((line.id, line.customer.name, line.expires_on))

    for record in records:
        if record.id not in missing_ids:
            continue
        if record.line_id is None or record.line_id in {line.id for line, _ in closable}:
            await session.delete(record)

    await session.flush()
    return report
