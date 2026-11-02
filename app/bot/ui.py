from __future__ import annotations

import html
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Line, Platform, Service
from app.services.months import days_left
from app.services.queries import CustomerOverview, panel_for
from app.services.text import format_eur

SERVICE_LABELS = {
    Service.LIVE: "Live",
    Service.LIVE_VOD: "Live + VOD",
}

PLATFORM_SHORT = {
    Platform.LIVE_8TVPLUS: "8Plus",
    Platform.LIVE_HALLO: "Live",
    Platform.VOD_NEWVOD: "VOD",
}


def esc(value: object) -> str:
    return html.escape(str(value))


def italian_date(value: date) -> str:
    return f"{value:%d/%m/%Y}"


def line_title(line: Line) -> str:
    name = line.customer.name
    if line.label:
        return f"{name} · {line.label}"
    return f"{name} · Linea unica"


def expiry_phrase(line: Line, today: date) -> str:
    delta = days_left(line.expires_on, today)
    if delta < 0:
        days = abs(delta)
        unit = "giorno" if days == 1 else "giorni"
        return f"scaduta il {italian_date(line.expires_on)} ({days} {unit} fa)"
    if delta == 0:
        return "scade oggi"
    if delta == 1:
        return "scade domani"
    return f"scade il {italian_date(line.expires_on)} (tra {delta} giorni)"


def line_row(line: Line, today: date) -> str:
    price = format_eur(line.monthly_price_cents)
    return f"• {esc(line_title(line))} — {esc(expiry_phrase(line, today))} · {price}"


def line_card(line: Line, today: date) -> str:
    customer = line.customer
    parts = [
        f"🎬 <b>{esc(line_title(line))}</b>",
    ]
    second: list[str] = []
    if customer.alias:
        second.append(f'alias: "{esc(customer.alias)}"')
    second.append("Diretto" if line.reseller is None else esc(line.reseller.name))
    parts.append(" · ".join(second))

    service = SERVICE_LABELS.get(line.service, line.service.value)
    price = format_eur(line.monthly_price_cents)
    price_note = " · prezzo da impostare" if line.monthly_price_cents == 0 else ""
    parts.append(f"{service} · {price}/mese{price_note}")

    live = panel_for(line, Platform.LIVE_HALLO) or panel_for(line, Platform.LIVE_8TVPLUS)
    vod = panel_for(line, Platform.VOD_NEWVOD)
    technical: list[str] = []
    if live is not None:
        technical.append(f"Live: <code>{esc(live.username)}</code>")
    if vod is not None:
        technical.append(f"VOD: <code>{esc(vod.username)}</code>")
    if technical:
        parts.append(" · ".join(technical))

    parts.append(f"<b>{esc(expiry_phrase(line, today))}</b>")
    if getattr(line, "no_renewal", False):
        parts.append("🚫 <b>Da non rinnovare</b>")
    if line.notes:
        parts.append(f"<i>{esc(line.notes)}</i>")
    return "\n".join(parts)


def menu_keyboard(*, is_owner: bool, is_reseller: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📅 Scadenze", callback_data="menu:due"),
            InlineKeyboardButton("🔎 Cerca", callback_data="menu:search"),
        ]
    ]
    if is_owner:
        rows.append(
            [
                InlineKeyboardButton("🆕 Nuovo cliente", callback_data="menu:new"),
                InlineKeyboardButton("💰 Incassi", callback_data="menu:income"),
            ]
        )
        rows.append([InlineKeyboardButton("👥 Rivenditori", callback_data="menu:resellers")])
    if is_reseller:
        rows.append([InlineKeyboardButton("🔁 Richiedi rinnovo", callback_data="menu:request")])
    if is_owner:
        rows.append([InlineKeyboardButton("⚙️ Impostazioni", callback_data="menu:settings")])
    return InlineKeyboardMarkup(rows)


def line_keyboard(line: Line, *, is_owner: bool, is_reseller: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if is_owner:
        rows.append(
            [
                InlineKeyboardButton("✅ Rinnova", callback_data=f"renew:{line.id}:pick"),
                InlineKeyboardButton("💶 Prezzo", callback_data=f"price:{line.id}"),
            ]
        )
        rows.append([InlineKeyboardButton("✏️ Modifica", callback_data=f"edit:{line.id}")])
        # La marcatura si mette e si toglie anche qui: il reminder da solo non
        # basta per una linea lontana dalla scadenza.
        if getattr(line, "no_renewal", False):
            rows.append(
                [InlineKeyboardButton("↩️ Togli «non rinnova»", callback_data=f"nr:{line.id}:clear")]
            )
        else:
            rows.append(
                [InlineKeyboardButton("🚫 Non rinnovare", callback_data=f"nr:{line.id}:set")]
            )
    rows.append(
        [
            InlineKeyboardButton("🕘 Storico", callback_data=f"history:{line.id}"),
            InlineKeyboardButton("👤 Cliente", callback_data=f"cust:{line.customer_id}"),
        ]
    )
    rows.append([InlineKeyboardButton("🔎 Cerca", callback_data="menu:search")])
    if is_reseller:
        rows.append([InlineKeyboardButton("🔁 Richiedi rinnovo", callback_data=f"rq:{line.id}")])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def months_keyboard(line: Line, presets: tuple[int, ...]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(f"{months} m", callback_data=f"renew:{line.id}:{months}")
        for months in presets
    ]
    rows = [buttons[index : index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton("Altro…", callback_data=f"renew:{line.id}:other")])
    rows.append([InlineKeyboardButton("⬅️ Annulla", callback_data=f"card:{line.id}")])
    return InlineKeyboardMarkup(rows)


def undo_keyboard(renewal_id: int, line_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("↩️ Annulla", callback_data=f"undo:{renewal_id}")],
            [InlineKeyboardButton("Apri scheda", callback_data=f"card:{line_id}")],
        ]
    )


def results_keyboard(lines: list[Line], today: date) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{line_title(line)} · {line.expires_on:%d/%m}",
                callback_data=f"card:{line.id}",
            )
        ]
        for line in lines
    ]

    # Quando un cliente ha più linee nei risultati, offre la scheda unica.
    counts: dict[int, int] = {}
    names: dict[int, str] = {}
    for line in lines:
        counts[line.customer_id] = counts.get(line.customer_id, 0) + 1
        names[line.customer_id] = line.customer.name
    for customer_id, count in counts.items():
        if count > 1:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"👤 {names[customer_id]} · {count} linee",
                        callback_data=f"cust:{customer_id}",
                    )
                ]
            )

    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def customer_card(overview: CustomerOverview, today: date, *, show_hidden: bool = True) -> str:
    """Scheda cliente: tutte le linee attive con il totale mensile.

    A un rivenditore non si dice quante linee dello stesso cliente esistono
    fuori dalla sua vista: è un dato che non gli serve.
    """
    customer = overview.customer
    parts = [f"👤 <b>{esc(customer.name)}</b>"]
    if customer.alias:
        parts.append(f'alias: "{esc(customer.alias)}"')

    word = "linea attiva" if overview.active_lines == 1 else "linee attive"
    parts.append(
        f"<b>{overview.active_lines} {word}</b> · {format_eur(overview.monthly_cents)}/mese"
    )
    if show_hidden and overview.hidden_lines:
        hidden = "linea non visibile" if overview.hidden_lines == 1 else "linee non visibili"
        parts.append(f"<i>{overview.hidden_lines} {hidden} a te</i>")

    if overview.lines:
        parts.append("")
        for line in overview.lines:
            label = line.label or "Linea unica"
            parts.append(
                f"• {esc(label)} — {esc(expiry_phrase(line, today))} · "
                f"{format_eur(line.monthly_price_cents)}"
            )
    else:
        parts.append("")
        parts.append("Nessuna linea attiva.")

    if customer.notes:
        parts.append("")
        parts.append(f"_{esc(customer.notes)}_")
    return "\n".join(parts)


def customer_keyboard(overview: CustomerOverview) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"{line.label or 'Linea unica'} · {line.expires_on:%d/%m}",
                callback_data=f"card:{line.id}",
            )
        ]
        for line in overview.lines
    ]
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)
