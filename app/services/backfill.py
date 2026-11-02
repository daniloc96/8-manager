from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Customer, Line, Reseller
from app.services.text import format_eur, normalize_key, parse_price_to_cents


class BackfillError(ValueError):
    """Riga di backfill non valida."""


@dataclass(slots=True)
class BackfillRow:
    line_id: int
    reseller: str
    price_cents: int


@dataclass(slots=True)
class BackfillChange:
    line_id: int
    customer: str
    label: str
    old_price: int
    new_price: int
    old_reseller: str | None
    new_reseller: str | None

    def describe(self) -> str:
        reseller = f"{self.old_reseller or 'Diretto'} → {self.new_reseller or 'Diretto'}"
        return (
            f"{self.line_id:>3} · {self.customer[:30]:<30} · "
            f"{format_eur(self.old_price)} → {format_eur(self.new_price)} · {reseller}"
        )


@dataclass(slots=True)
class BackfillReport:
    changes: list[BackfillChange] = field(default_factory=list)
    unknown_lines: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    applied: bool = False


def parse_rows(text: str) -> list[BackfillRow]:
    """Legge righe `linea;rivenditore;prezzo` (separatore ; o virgola)."""
    rows: list[BackfillRow] = []
    reader = csv.reader(io.StringIO(text), delimiter=";" if ";" in text else ",")
    for number, raw in enumerate(reader, start=1):
        if not raw or not any(cell.strip() for cell in raw):
            continue
        if raw[0].strip().startswith("#"):
            continue
        if raw[0].strip().lower() in {"linea", "line", "id"}:
            continue
        if len(raw) < 3:
            raise BackfillError(f"riga {number}: servono linea, rivenditore e prezzo")
        try:
            line_id = int(raw[0].strip())
        except ValueError as exc:
            raise BackfillError(f"riga {number}: linea non numerica ({raw[0]!r})") from exc
        reseller = raw[1].strip()
        price = parse_price_to_cents(raw[2])
        if price is None:
            raise BackfillError(f"riga {number}: prezzo non valido ({raw[2]!r})")
        rows.append(BackfillRow(line_id=line_id, reseller=reseller, price_cents=price))
    return rows


def read_rows(path: Path) -> list[BackfillRow]:
    return parse_rows(path.read_text(encoding="utf-8"))


DIRECT_LABELS = {"", "-", "diretto", "direct", "0"}


def _is_direct(name: str) -> bool:
    return name.strip().lower() in DIRECT_LABELS


async def _find_reseller(session: AsyncSession, name: str) -> Reseller | None:
    if _is_direct(name):
        return None
    return await session.scalar(select(Reseller).where(Reseller.name_key == normalize_key(name)))


async def _resolve_reseller(session: AsyncSession, name: str) -> Reseller | None:
    """Trova o crea il rivenditore. Solo in fase di scrittura."""
    record = await _find_reseller(session, name)
    if record is not None or _is_direct(name):
        return record
    cleaned = name.strip()
    record = Reseller(name=cleaned, name_key=normalize_key(cleaned), notify=True, active=True)
    session.add(record)
    await session.flush()
    return record


async def apply_backfill(
    session: AsyncSession,
    rows: list[BackfillRow],
    *,
    apply: bool = False,
) -> BackfillReport:
    """Aggiorna prezzo e rivenditore di ogni linea indicata."""
    report = BackfillReport(applied=apply)
    seen: set[int] = set()

    for row in rows:
        if row.line_id in seen:
            report.errors.append(f"linea {row.line_id}: indicata più di una volta")
            continue
        seen.add(row.line_id)

        line = await session.get(Line, row.line_id)
        if line is None or line.deleted_at is not None:
            report.unknown_lines.append(row.line_id)
            continue

        existing = await _find_reseller(session, row.reseller)
        customer = line.customer
        report.changes.append(
            BackfillChange(
                line_id=line.id,
                customer=customer.name if customer else "",
                label=line.label or "",
                old_price=line.monthly_price_cents,
                new_price=row.price_cents,
                old_reseller=line.reseller.name if line.reseller else None,
                new_reseller=(
                    None
                    if _is_direct(row.reseller)
                    else (existing.name if existing else row.reseller.strip())
                ),
            )
        )
        if not apply:
            continue

        reseller = await _resolve_reseller(session, row.reseller)

        old = {
            "monthly_price_cents": line.monthly_price_cents,
            "reseller_id": line.reseller_id,
        }
        line.monthly_price_cents = row.price_cents
        line.reseller_id = reseller.id if reseller else None
        session.add(
            AuditLog(
                action="backfill_commercial",
                entity="line",
                entity_id=line.id,
                payload={
                    "old": old,
                    "new": {
                        "monthly_price_cents": row.price_cents,
                        "reseller": row.reseller,
                    },
                },
            )
        )

    if apply:
        await session.flush()
    return report


async def ensure_reseller(
    session: AsyncSession,
    *,
    name: str,
    telegram_user_id: int | None = None,
    default_price_cents: int | None = None,
) -> Reseller:
    """Crea o aggiorna un rivenditore, collegando l'ID Telegram."""
    cleaned = name.strip()
    if not cleaned:
        raise BackfillError("il nome del rivenditore è obbligatorio")
    key = normalize_key(cleaned)

    by_telegram = None
    if telegram_user_id is not None:
        by_telegram = await session.scalar(
            select(Reseller).where(Reseller.telegram_user_id == telegram_user_id)
        )
    by_name = await session.scalar(select(Reseller).where(Reseller.name_key == key))

    if by_telegram is not None and by_name is not None and by_telegram.id != by_name.id:
        raise BackfillError(
            f"l'ID Telegram {telegram_user_id} è già di «{by_telegram.name}» "
            f"e «{by_name.name}» esiste già come rivenditore"
        )

    record = by_telegram or by_name
    if record is None:
        record = Reseller(name=cleaned, name_key=key, active=True, notify=True)
        session.add(record)

    record.name = cleaned
    record.name_key = key
    record.active = True
    if telegram_user_id is not None:
        record.telegram_user_id = telegram_user_id
    if default_price_cents is not None:
        record.default_monthly_price_cents = default_price_cents

    await session.flush()
    return record


async def rename_customer(session: AsyncSession, *, line_id: int, name: str) -> Customer:
    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise BackfillError(f"linea {line_id} non trovata")
    customer = line.customer
    cleaned = name.strip()
    if not cleaned:
        raise BackfillError("il nome del cliente è obbligatorio")
    old = customer.name
    customer.name = cleaned
    customer.search_key = f"{normalize_key(cleaned)} {normalize_key(customer.alias)}".strip()
    session.add(
        AuditLog(
            action="customer_renamed",
            entity="line",
            entity_id=line.id,
            payload={"old": old, "new": cleaned},
        )
    )
    await session.flush()
    return customer
