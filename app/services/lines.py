from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Customer, Line, Renewal
from app.models.enums import RenewalSource, Service
from app.services.anagrafica import ALIAS_MAX, LABEL_MAX
from app.services.months import MAX_MONTHS, MIN_MONTHS, add_months, plan_renewal
from app.services.text import customer_search_key


@dataclass(frozen=True, slots=True)
class LineDraft:
    """Dati minimi per creare cliente + prima linea in un colpo solo."""

    name: str
    service: Service
    monthly_price_cents: int
    months: int
    alias: str | None = None
    label: str | None = None
    reseller_id: int | None = None
    notes: str | None = None
    start_on: date | None = None
    # Dati tecnici: sono i valori che l'owner copia nel pannello. Non creano
    # righe `panel_lines`, che nascono dal sync quando il pannello le trova.
    panel_external_id: str | None = None
    panel_access_code: str | None = None


def build_customer_and_line(draft: LineDraft, today: date) -> tuple[Customer, Line]:
    if not draft.name.strip():
        raise ValueError("il nome del cliente è obbligatorio")
    if not MIN_MONTHS <= draft.months <= MAX_MONTHS:
        raise ValueError(f"la durata deve essere tra {MIN_MONTHS} e {MAX_MONTHS} mesi")
    if draft.monthly_price_cents < 0:
        raise ValueError("il prezzo non può essere negativo")

    label = draft.label.strip() if draft.label and draft.label.strip() else None
    if label is not None and len(label) > LABEL_MAX:
        raise ValueError(f"l'etichetta supera {LABEL_MAX} caratteri")
    alias = draft.alias.strip() if draft.alias and draft.alias.strip() else None
    if alias is not None and len(alias) > ALIAS_MAX:
        raise ValueError(f"l'alias supera {ALIAS_MAX} caratteri")

    start = draft.start_on or today
    expires = add_months(start, draft.months, start.day)

    customer = Customer(
        name=draft.name.strip(),
        alias=alias,
        search_key=customer_search_key(draft.name, alias),
        notes=draft.notes,
    )
    line = Line(
        customer=customer,
        label=label,
        reseller_id=draft.reseller_id,
        service=draft.service,
        monthly_price_cents=draft.monthly_price_cents,
        started_on=start,
        expires_on=expires,
        anchor_day=start.day,
    )
    return customer, line


async def create_customer_and_line(
    session: AsyncSession, draft: LineDraft, today: date
) -> tuple[Customer, Line]:
    customer, line = build_customer_and_line(draft, today)
    session.add_all([customer, line])
    await session.flush()
    return customer, line


async def renew_line(
    session: AsyncSession,
    line: Line,
    *,
    months: int,
    today: date,
    source: RenewalSource,
    actor_telegram_id: int | None = None,
    monthly_price_cents: int | None = None,
    total_cents: int | None = None,
    note: str | None = None,
) -> Renewal:
    """Registra un rinnovo e sposta la scadenza della linea.

    Il prezzo salvato sul rinnovo è lo snapshot di quello che l'owner deve
    incassare; il totale di default è prezzo per mesi, ma può essere corretto.
    """
    price = line.monthly_price_cents if monthly_price_cents is None else monthly_price_cents
    if price < 0:
        raise ValueError("il prezzo non può essere negativo")

    plan = plan_renewal(line.expires_on, line.anchor_day, today, months)
    total = price * months if total_cents is None else total_cents
    if total < 0:
        raise ValueError("il totale non può essere negativo")

    renewal = Renewal(
        line=line,
        months=months,
        monthly_price_cents=price,
        total_cents=total,
        period_start=plan.period_start,
        period_end=plan.period_end,
        source=source,
        actor_telegram_id=actor_telegram_id,
        note=note,
    )
    line.expires_on = plan.period_end
    line.anchor_day = plan.anchor_day
    line.monthly_price_cents = price
    # Rinnovare significa rinnovare: la marcatura operativa non ha più senso.
    if line.no_renewal:
        line.no_renewal = False
        line.no_renewal_at = None
        session.add(
            AuditLog(
                action="line_no_renewal_clear",
                entity="line",
                entity_id=line.id,
                payload={
                    "old": True,
                    "new": False,
                    "reason": "renewed",
                    "actor_telegram_id": actor_telegram_id,
                    "at": datetime.now().isoformat(timespec="seconds"),
                },
            )
        )
    session.add(renewal)
    await session.flush()
    return renewal
