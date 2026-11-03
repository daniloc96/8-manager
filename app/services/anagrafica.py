from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Customer, Line, PanelLine, Platform, Reseller
from app.services.text import customer_search_key

ALIAS_MAX = 160
LABEL_MAX = 80
NOTES_MAX = 500

# Campi tecnici modificabili a mano sulla riga di pannello. La chiave è il nome
# della colonna, il valore la lunghezza massima: niente setattr arbitrari.
PANEL_FIELDS = {"external_id": 120, "access_code": 160}


class AnagraficaError(ValueError):
    """Modifica rifiutata: valore non ammesso o in conflitto con un'altra linea."""


def _single_line(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


def _multiline(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _check_length(value: str | None, limit: int, what: str) -> None:
    if value is not None and len(value) > limit:
        raise AnagraficaError(f"{what} troppo lungo: massimo {limit} caratteri.")


def _audit(
    session: AsyncSession,
    *,
    action: str,
    entity: str,
    entity_id: int,
    payload: dict,
) -> None:
    session.add(AuditLog(action=action, entity=entity, entity_id=entity_id, payload=payload))


async def _active_customer(session: AsyncSession, customer_id: int) -> Customer:
    customer = await session.get(Customer, customer_id)
    if customer is None or customer.deleted_at is not None:
        raise AnagraficaError("cliente non trovato.")
    return customer


async def _active_line(session: AsyncSession, line_id: int) -> Line:
    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise AnagraficaError("linea non trovata o disattivata.")
    return line


async def set_customer_alias(
    session: AsyncSession, customer_id: int, alias: str | None
) -> tuple[Customer, bool]:
    """Aggiorna l'alias del cliente e la chiave di ricerca. False = non cambiato."""
    customer = await _active_customer(session, customer_id)
    new = _single_line(alias)
    _check_length(new, ALIAS_MAX, "Alias")
    previous = customer.alias
    if new == previous:
        return customer, False

    customer.alias = new
    # La ricerca libera usa search_key: senza questo l'alias nuovo non si trova.
    customer.search_key = customer_search_key(customer.name, new)
    _audit(
        session,
        action="customer_alias_set",
        entity="customer",
        entity_id=customer.id,
        payload={"old": previous, "new": new},
    )
    await session.flush()
    return customer, True


async def set_line_label(
    session: AsyncSession, line_id: int, label: str | None
) -> tuple[Line, bool]:
    """Etichetta della linea ("Linea 1"). Unica fra le linee attive del cliente."""
    line = await _active_line(session, line_id)
    new = _single_line(label)
    _check_length(new, LABEL_MAX, "Etichetta")

    if new is not None:
        duplicate = await session.scalar(
            select(Line).where(
                Line.customer_id == line.customer_id,
                Line.label == new,
                Line.deleted_at.is_(None),
                Line.id != line.id,
            )
        )
        if duplicate is not None:
            raise AnagraficaError(
                f"il cliente ha già una linea «{new}»: scegli un'etichetta diversa."
            )

    previous = line.label
    if new == previous:
        return line, False

    line.label = new
    _audit(
        session,
        action="line_label_set",
        entity="line",
        entity_id=line.id,
        payload={"old": previous, "new": new},
    )
    await session.flush()
    return line, True


async def set_line_notes(
    session: AsyncSession, line_id: int, notes: str | None
) -> tuple[Line, bool]:
    line = await _active_line(session, line_id)
    new = _multiline(notes)
    _check_length(new, NOTES_MAX, "Nota")

    previous = line.notes
    if new == previous:
        return line, False

    line.notes = new
    _audit(
        session,
        action="line_notes_set",
        entity="line",
        entity_id=line.id,
        payload={"old": previous, "new": new},
    )
    await session.flush()
    return line, True


async def set_panel_field(
    session: AsyncSession,
    line_id: int,
    platform: Platform,
    field: str,
    value: str | None,
) -> tuple[PanelLine, bool]:
    """Aggiorna `external_id` o `access_code` della riga di un pannello."""
    if field not in PANEL_FIELDS:
        raise AnagraficaError(f"campo tecnico non modificabile: {field}.")

    line = await _active_line(session, line_id)
    panel = await session.scalar(
        select(PanelLine).where(
            PanelLine.line_id == line.id,
            PanelLine.platform == platform,
        )
    )
    if panel is None:
        raise AnagraficaError("la linea non ha una riga su questo pannello.")

    new = _single_line(value)
    _check_length(new, PANEL_FIELDS[field], field)

    previous = getattr(panel, field)
    if new == previous:
        return panel, False

    setattr(panel, field, new)
    _audit(
        session,
        action=f"panel_{field}_set",
        entity="panel_line",
        entity_id=panel.id,
        payload={"platform": platform.value, "line_id": line.id, "old": previous, "new": new},
    )
    await session.flush()
    return panel, True


async def _resolve_reseller(session: AsyncSession, reseller_id: int | None) -> Reseller | None:
    """Rivenditore attivo, oppure None per «Diretto»."""
    if reseller_id is None:
        return None
    reseller = await session.get(Reseller, reseller_id)
    if reseller is None or not reseller.active:
        raise AnagraficaError("rivenditore non trovato o non attivo.")
    return reseller


async def set_line_reseller(
    session: AsyncSession, line_id: int, reseller_id: int | None
) -> tuple[Line, bool]:
    """Passa una linea a un rivenditore, oppure la riporta a «Diretto».

    Il rivenditore vive sulla linea (i pannelli non lo conoscono), quindi il sync
    non lo sovrascrive: questa modifica sopravvive.
    """
    line = await _active_line(session, line_id)
    reseller = await _resolve_reseller(session, reseller_id)
    new_id = reseller.id if reseller is not None else None

    if line.reseller_id == new_id:
        return line, False

    previous = line.reseller_id
    line.reseller_id = new_id
    _audit(
        session,
        action="line_reseller_set",
        entity="line",
        entity_id=line.id,
        payload={
            "old": previous,
            "new": new_id,
            "reseller": reseller.name if reseller is not None else None,
        },
    )
    await session.flush()
    return line, True


async def set_customer_reseller(
    session: AsyncSession, customer_id: int, reseller_id: int | None
) -> tuple[int, int]:
    """Porta tutte le linee attive del cliente a un rivenditore (o a Diretto).

    Ritorna `(cambiate, considerate)`: un cliente con più linee si sposta in un
    colpo solo, senza doverle toccare una per una.
    """
    customer = await _active_customer(session, customer_id)
    reseller = await _resolve_reseller(session, reseller_id)
    new_id = reseller.id if reseller is not None else None
    name = reseller.name if reseller is not None else None

    lines = list(
        (
            await session.scalars(
                select(Line).where(Line.customer_id == customer.id, Line.deleted_at.is_(None))
            )
        ).all()
    )

    changed = 0
    for line in lines:
        if line.reseller_id == new_id:
            continue
        previous = line.reseller_id
        line.reseller_id = new_id
        changed += 1
        _audit(
            session,
            action="line_reseller_set",
            entity="line",
            entity_id=line.id,
            payload={
                "old": previous,
                "new": new_id,
                "reseller": name,
                "via": "customer",
            },
        )

    if changed:
        _audit(
            session,
            action="customer_reseller_set",
            entity="customer",
            entity_id=customer.id,
            payload={"new": new_id, "reseller": name, "lines": changed},
        )
        await session.flush()
    return changed, len(lines)
