from __future__ import annotations

import html
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import Line, Platform, Service
from app.services.months import days_left
from app.services.queries import panel_for
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
    if line.notes:
        parts.append(f"_{esc(line.notes)}_")
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
    rows.append(
        [
            InlineKeyboardButton("🕘 Storico", callback_data=f"history:{line.id}"),
            InlineKeyboardButton("🔎 Cerca", callback_data="menu:search"),
        ]
    )
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
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)
