from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from app.models.enums import LineStatus

MIN_MONTHS = 1
MAX_MONTHS = 36


@dataclass(frozen=True, slots=True)
class RenewalPlan:
    """Dove finisce il periodo dopo un rinnovo.

    continuation=True: la linea era ancora attiva, i mesi si sommano alla
    scadenza corrente. False: era scaduta, il periodo riparte da oggi.
    """

    period_start: date
    period_end: date
    anchor_day: int
    continuation: bool


def add_months(base: date, months: int, anchor_day: int | None = None) -> date:
    """Somma mesi di calendario, con l'ancora per non derivare a fine mese.

    31/01 + 1 mese -> 28/02 (o 29/02), ma con anchor_day=31 il mese dopo
    torna al 31: 28/02 + 1 -> 31/03, non 28/03.
    """
    if months < 0:
        raise ValueError("months must be >= 0")
    anchor = base.day if anchor_day is None else anchor_day
    if not 1 <= anchor <= 31:
        raise ValueError("anchor_day must be between 1 and 31")

    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(anchor, last_day))


def plan_renewal(
    expires_on: date,
    anchor_day: int,
    today: date,
    months: int,
) -> RenewalPlan:
    if not MIN_MONTHS <= months <= MAX_MONTHS:
        raise ValueError(f"months must be between {MIN_MONTHS} and {MAX_MONTHS}")

    if expires_on >= today:
        start = expires_on
        anchor = anchor_day
        continuation = True
    else:
        start = today
        anchor = today.day
        continuation = False

    return RenewalPlan(
        period_start=start,
        period_end=add_months(start, months, anchor),
        anchor_day=anchor,
        continuation=continuation,
    )


def days_left(expires_on: date, today: date) -> int:
    return (expires_on - today).days


def status_for(expires_on: date, today: date, warn_days: int = 3) -> LineStatus:
    if expires_on < today:
        return LineStatus.EXPIRED
    if (expires_on - today).days <= warn_days:
        return LineStatus.EXPIRING
    return LineStatus.ACTIVE


def months_between(start: date, end: date, anchor_day: int | None = None) -> int | None:
    """Quanti mesi interi separano due date, o None se il salto non è esatto.

    Serve al sync per capire che una scadenza passata da 13/11 a 13/02
    significa "rinnovata di 3 mesi".
    """
    if end < start:
        return None
    anchor = start.day if anchor_day is None else anchor_day
    for months in range(0, MAX_MONTHS + 1):
        if add_months(start, months, anchor) == end:
            return months
    return None
