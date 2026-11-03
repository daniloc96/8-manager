from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import access_for
from app.db import session_scope
from app.models import Platform
from app.services.anagrafica import (
    AnagraficaError,
    set_customer_alias,
    set_line_label,
    set_line_notes,
    set_line_reseller,
    set_panel_field,
)
from app.services.queries import get_line

logger = logging.getLogger(__name__)

# field del callback -> campo del servizio
TEXT_FIELDS = {"alias": "alias", "label": "label", "notes": "notes"}
PANEL_CALLBACKS = {"ext": "external_id", "code": "access_code"}

FIELD_LABELS = {
    "alias": "Alias",
    "label": "Etichetta",
    "notes": "Note",
    "external_id": "ID pannello",
    "access_code": "Codice",
}


def _cancel_row(line_id: int) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("⬅️ Annulla", callback_data=f"edit:{line_id}:cancel")]


def _picker(line) -> tuple[str, InlineKeyboardMarkup]:
    """Elenco dei campi modificabili, uno alla volta."""
    body = [
        f"✏️ <b>Modifica</b> · {ui.esc(ui.line_title(line))}",
        "",
        "Scegli il campo da cambiare.",
    ]
    who = "Diretto" if line.reseller is None else line.reseller.name
    rows = [
        [
            InlineKeyboardButton("Alias", callback_data=f"edit:{line.id}:alias"),
            InlineKeyboardButton("Etichetta", callback_data=f"edit:{line.id}:label"),
        ],
        [InlineKeyboardButton("Note", callback_data=f"edit:{line.id}:notes")],
        [InlineKeyboardButton(f"🏷 Rivenditore · {who}", callback_data=f"edit:{line.id}:reseller")],
    ]
    if line.panel_lines:
        rows.append(
            [InlineKeyboardButton("🔧 Dati pannello", callback_data=f"edit:{line.id}:tech")]
        )
    rows.append([InlineKeyboardButton("⬅️ Scheda", callback_data=f"card:{line.id}")])
    return "\n".join(body), InlineKeyboardMarkup(rows)


def _tech_picker(line) -> tuple[str, InlineKeyboardMarkup]:
    body = [f"🔧 <b>Dati pannello</b> · {ui.esc(ui.line_title(line))}", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for panel in sorted(line.panel_lines, key=lambda item: item.platform.value):
        short = ui.PLATFORM_SHORT.get(panel.platform, panel.platform.value)
        id_text = f"<code>{ui.esc(panel.external_id)}</code>" if panel.external_id else "—"
        code_text = f"<code>{ui.esc(panel.access_code)}</code>" if panel.access_code else "—"
        body.append(f"• <b>{ui.esc(short)}</b> · <code>{ui.esc(panel.username)}</code>")
        body.append(f"  ID {id_text} · codice {code_text}")
        rows.append(
            [
                InlineKeyboardButton(
                    f"ID {short}", callback_data=f"edit:{line.id}:ext:{panel.platform.value}"
                ),
                InlineKeyboardButton(
                    f"Codice {short}", callback_data=f"edit:{line.id}:code:{panel.platform.value}"
                ),
            ]
        )
    body.extend(
        [
            "",
            "<i>Il pannello vince: se lo conosce, il prossimo /sync riporta "
            "ID e codice ai suoi valori.</i>",
        ]
    )
    rows.append([InlineKeyboardButton("⬅️ Modifica", callback_data=f"edit:{line.id}")])
    return "\n".join(body), InlineKeyboardMarkup(rows)


async def _ask_reseller(query, line_id: int) -> None:
    """Elenco dei rivenditori attivi, più «Diretto» per togliere l'assegnazione."""
    from sqlalchemy import select

    from app.models import Reseller
    from app.services.queries import get_line

    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        resellers = list(
            (await session.scalars(select(Reseller).where(Reseller.active.is_(True)))).all()
        )
        current = "Diretto" if line.reseller is None else line.reseller.name
        title = ui.line_title(line)

    rows = [[InlineKeyboardButton("Diretto (io)", callback_data=f"edit:{line_id}:rset:0")]]
    for reseller in resellers:
        mark = "✓ " if reseller.name == current else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark}{reseller.name}", callback_data=f"edit:{line_id}:rset:{reseller.id}"
                )
            ]
        )
    rows.append(_cancel_row(line_id))
    await query.edit_message_text(
        f"🏷 <b>Rivenditore</b> · {ui.esc(title)}\n\n"
        f"Ora: <b>{ui.esc(current)}</b>\n"
        "Scegli a chi va la linea.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


def _prompt(line, field: str, platform: Platform | None = None) -> str:
    title = ui.esc(ui.line_title(line))
    if field == "alias":
        return (
            f"✏️ <b>Alias</b> · {title}\n\n"
            "Scrivi il nuovo alias, oppure <code>-</code> per toglierlo."
        )
    if field == "label":
        return (
            f"✏️ <b>Etichetta</b> · {title}\n\n"
            "Per esempio «Linea 1». <code>-</code> per tornare a «Linea unica»."
        )
    if field == "notes":
        return f"✏️ <b>Note</b> · {title}\n\nScrivi la nota, oppure <code>-</code> per cancellarla."
    short = ui.PLATFORM_SHORT.get(platform, platform.value if platform else "pannello")
    what = FIELD_LABELS.get(field, field)
    return (
        f"🔧 <b>{ui.esc(what)}</b> · {title}\n\n"
        f"Valore per <b>{ui.esc(short)}</b>. <code>-</code> per svuotarlo."
    )


async def _access_is_owner(update: Update) -> bool:
    async with session_scope() as session:
        access = await access_for(update, session)
    return access.is_owner and access.can_act


async def ask_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheda -> pulsante Modifica: mostra i campi."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not await _access_is_owner(update):
        await query.answer("Solo l'owner può modificare l'anagrafica.", show_alert=True)
        return
    await query.answer()
    _, raw_id, *_rest = query.data.split(":")
    line_id = int(raw_id)

    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        body, markup = _picker(line)

    await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _ask_tech(query, line_id: int) -> None:
    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        body, markup = _tech_picker(line)
    await query.edit_message_text(body, parse_mode=ParseMode.HTML, reply_markup=markup)


async def _start_edit(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    line_id: int,
    field: str,
    platform: Platform | None,
) -> None:
    async with session_scope() as session:
        line = await get_line(session, line_id)
        if line is None:
            await query.edit_message_text("Linea non trovata o disattivata.")
            return
        body = _prompt(line, field, platform)

    awaiting = f"lineedit:{line_id}:{field}"
    if platform is not None:
        awaiting = f"{awaiting}:{platform.value}"
    context.user_data["awaiting"] = awaiting
    await query.edit_message_text(
        body,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([_cancel_row(line_id)]),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    parts = query.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        return
    line_id = int(parts[1])
    action = parts[2] if len(parts) > 2 else ""

    if action == "cancel":
        context.user_data.pop("awaiting", None)
        from app.bot.lines import send_card

        await send_card(update, context, line_id)
        return

    if not action:
        await ask_field(update, context)
        return

    if not await _access_is_owner(update):
        await query.answer("Solo l'owner può modificare l'anagrafica.", show_alert=True)
        return
    await query.answer()

    if action == "tech":
        await _ask_tech(query, line_id)
        return

    if action == "reseller":
        await _ask_reseller(query, line_id)
        return

    if action == "rset" and len(parts) > 3 and parts[3].isdigit():
        from app.bot.lines import send_card

        reseller_id = int(parts[3]) or None
        async with session_scope() as session:
            try:
                line, changed = await set_line_reseller(session, line_id, reseller_id)
            except AnagraficaError as exc:
                await query.edit_message_text(f"⚠️ {ui.esc(exc)}")
                return
            who = "Diretto" if line.reseller is None else ui.esc(line.reseller.name)

        mark = "✅ Passata a" if changed else "🔄 Già assegnata a"
        note = f"{mark} <b>{who}</b>"
        await send_card(update, context, line_id, note=note)
        return

    if action in TEXT_FIELDS:
        await _start_edit(query, context, line_id, action, None)
        return

    if action in PANEL_CALLBACKS and len(parts) > 3:
        try:
            platform = Platform(parts[3])
        except ValueError:
            await query.edit_message_text("Pannello sconosciuto.")
            return
        await _start_edit(query, context, line_id, PANEL_CALLBACKS[action], platform)
        return


def _parse_value(text: str) -> str | None:
    """`-` svuota il campo, altrimenti vale il testo scritto."""
    stripped = text.strip()
    if stripped in {"", "-"}:
        return None
    return stripped


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Testo in attesa di una modifica anagrafica. True se consumato."""
    pending = context.user_data.get("awaiting")
    message = update.effective_message
    if not isinstance(pending, str) or not pending.startswith("lineedit:"):
        return False
    if message is None or message.text is None:
        return False

    parts = pending.split(":")
    if len(parts) < 3 or not parts[1].isdigit():
        context.user_data.pop("awaiting", None)
        return True
    line_id = int(parts[1])
    field = parts[2]
    platform = None
    if len(parts) > 3:
        try:
            platform = Platform(parts[3])
        except ValueError:
            context.user_data.pop("awaiting", None)
            await message.reply_html("Pannello sconosciuto: modifica annullata.")
            return True

    value = _parse_value(message.text)

    async with session_scope() as session:
        access = await access_for(update, session)
        if not (access.is_owner and access.can_act):
            context.user_data.pop("awaiting", None)
            await message.reply_html("Solo l'owner può modificare l'anagrafica.")
            return True

        line = await get_line(session, line_id)
        if line is None:
            context.user_data.pop("awaiting", None)
            await message.reply_html("Linea non trovata o disattivata.")
            return True

        try:
            if field in TEXT_FIELDS:
                if field == "alias":
                    _customer, changed = await set_customer_alias(session, line.customer_id, value)
                elif field == "label":
                    _line, changed = await set_line_label(session, line_id, value)
                else:
                    _line, changed = await set_line_notes(session, line_id, value)
            elif field in FIELD_LABELS:
                if platform is None:
                    raise AnagraficaError("pannello mancante.")
                _panel, changed = await set_panel_field(session, line_id, platform, field, value)
            else:
                raise AnagraficaError("campo non modificabile.")
        except AnagraficaError as exc:
            # Lo stato resta in attesa: si può correggere senza ripartire.
            await message.reply_html(
                f"⚠️ {ui.esc(exc)}\n\nRiprova, oppure premi Annulla.",
                reply_markup=InlineKeyboardMarkup([_cancel_row(line_id)]),
            )
            return True

        title = ui.line_title(line)

    context.user_data.pop("awaiting", None)
    label = FIELD_LABELS.get(field, field)
    if platform is not None:
        label = f"{label} {ui.PLATFORM_SHORT.get(platform, platform.value)}"

    if changed:
        shown = "—" if value is None else f"«{ui.esc(value)}»"
        body = f"✅ <b>{ui.esc(label)}</b> aggiornato\n\n{ui.esc(title)}\n{shown}"
    else:
        body = f"🔄 <b>{ui.esc(label)}</b> invariato\n\n{ui.esc(title)}"

    await message.reply_html(
        body,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Apri scheda", callback_data=f"card:{line_id}")]]
        ),
    )
    return True
