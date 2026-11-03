"""Menu a bottoni nel gruppo del rivenditore.

In gruppo i comandi indirizzati (`/scadenze@bot`, `/richiedi@bot`) funzionano ma
sono scomodi da ricordare: con `/start` compare una pulsantiera con le due cose
che servono davvero, senza prezzi e senza comandi dell'owner.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.bot import ui
from app.bot.auth import is_group
from app.config import get_settings
from app.db import session_scope
from app.jobs.reminders import _today, group_payload, group_rows
from app.models import Reseller
from app.services.queries import (
    customer_overview,
    lines_of_reseller,
    search_lines,
)

logger = logging.getLogger(__name__)

MAX_LINES = 12
PAGE_SIZE = 8
_MAX_RESULTS = 10
# Risultati in attesa di paginazione, per messaggio: la lista dei nomi non sta
# nel callback_data (64 byte) e non serve tenerla nel DB per qualche minuto.
_RESULTS_CACHE: dict[tuple[int, int], tuple[str, list[tuple[int, str]]]] = {}
_RESULTS_CACHE_LIMIT = 512
SEARCH_HINT = "gsearch"


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Scadenze", callback_data="gmenu:due"),
                InlineKeyboardButton("🔁 Rinnovo", callback_data="gmenu:renew"),
            ],
            [
                InlineKeyboardButton("🔎 Cerca cliente", callback_data="gmenu:search"),
                InlineKeyboardButton("❓ Aiuto", callback_data="gmenu:help"),
            ],
        ]
    )


def _back(label: str = "🏠 Menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="gmenu:home")]])


def _menu_text(name: str) -> str:
    return (
        f"👋 <b>{ui.esc(name)}</b>, cosa ti serve?\n\n"
        "📅 <b>Scadenze</b> — le linee in scadenza nei prossimi "
        f"{get_settings().reminder_days} giorni\n"
        "🔁 <b>Rinnovo</b> — scegli la linea e il periodo da richiedere\n"
        "🔎 <b>Cerca cliente</b> — quando le linee sono tante, cerca per nome\n\n"
        "Qui vedi solo le tue linee: i prezzi restano in privato."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """`/start` in gruppo: pulsantiera al posto dei comandi. True se gestito."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None or not is_group(chat.type):
        return False

    async with session_scope() as session:
        found = await _reseller_of_chat(update, session)
    if found is None:
        await message.reply_html(
            "Questo gruppo non è ancora collegato a un rivenditore.\n"
            "L'owner lo collega con <code>/collega Nome</code>."
        )
        return True
    _reseller_id, name = found

    await message.reply_html(_menu_text(name), reply_markup=_menu_keyboard())
    return True


async def _reseller_of_chat(update: Update, session) -> tuple[int, str] | None:
    """Il rivenditore collegato a questa chat, non chi sta premendo.

    Nel gruppo del rivenditore anche l'owner vede lo stesso menu: i permessi
    sulle azioni restano quelli di `group_request`.
    """
    chat = update.effective_chat
    if chat is None or not is_group(chat.type):
        return None
    reseller = await session.scalar(
        select(Reseller).where(
            Reseller.group_chat_id == chat.id,
            Reseller.active.is_(True),
        )
    )
    if reseller is None:
        return None
    return reseller.id, reseller.name


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()
    editing = ui.Editor(query)

    async with session_scope() as session:
        found = await _reseller_of_chat(update, session)
        if found is None:
            await editing.edit_text(
                "Questo gruppo non è collegato a un rivenditore: chiedi all'owner."
            )
            return
        reseller_id, name = found

        if query.data == "gmenu:home":
            await editing.edit_text(
                _menu_text(name),
                parse_mode=ParseMode.HTML,
                reply_markup=_menu_keyboard(),
            )
            return

        if query.data == "gmenu:due":
            rows = await group_rows(_today(), reseller_id=reseller_id)
            payload = group_payload(rows)
            if payload is None:
                await editing.edit_text(
                    f"✅ Nessuna scadenza nei prossimi {get_settings().reminder_days} giorni.",
                    reply_markup=_back(),
                )
                return
            body, markup = payload
            with_menu = InlineKeyboardMarkup(
                [
                    *markup.inline_keyboard,
                    [InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")],
                ]
            )
            await editing.edit_text(body, parse_mode=ParseMode.HTML, reply_markup=with_menu)
            return

        if query.data == "gmenu:renew":
            lines = (await lines_of_reseller(session, reseller_id))[:MAX_LINES]
            if not lines:
                await editing.edit_text(
                    "Non ci sono linee attive da rinnovare.", reply_markup=_back()
                )
                return
            buttons = [
                [
                    InlineKeyboardButton(
                        f"{ui.line_title(line)} · {line.expires_on:%d/%m}"[:60],
                        callback_data=f"greq:{line.id}",
                    )
                ]
                for line in lines
            ]
            buttons.append([InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")])
            await editing.edit_text(
                "🔁 <b>Quale linea vuoi rinnovare?</b>\n\n"
                "Scegli la linea, poi il periodo: la richiesta parte solo dopo la conferma.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if query.data == "gmenu:search":
            context.user_data["awaiting"] = SEARCH_HINT
            await editing.edit_text(
                "🔎 <b>Chi cerchi?</b>\n\nScrivi il nome del cliente, anche solo una parte.\n"
                "<i>Meglio se rispondi a questo messaggio</i>: con la privacy dei bot "
                "attiva Telegram mi gira solo comandi e risposte.",
                parse_mode=ParseMode.HTML,
                reply_markup=_back(),
            )
            return

        if query.data.startswith("gcard:"):
            await _show_card(editing, session, query.data, reseller_id)
            return

        if query.data.startswith("gdue:"):
            await _show_due(editing, session, query.data, reseller_id)
            return

        if query.data.startswith("gcod:"):
            await _show_codes(editing, session, query.data, reseller_id)
            return

        if query.data.startswith("grenew:"):
            await _show_customer_renew(editing, session, query.data, reseller_id)
            return

        await editing.edit_text(
            "❓ <b>Come funziona</b>\n\n"
            "📅 <b>Scadenze</b> mostra le linee tue in scadenza, con il pulsante "
            "«Richiedi» su ognuna.\n"
            "🔁 <b>Rinnovo</b> fa scegliere linea e periodo; prima di inviare ti "
            "chiedo conferma.\n"
            "🔎 <b>Cerca cliente</b> cerca per nome e apre scheda o rinnovo di quel "
            "cliente.\n\n"
            "Fuori dal menu restano i comandi <code>/scadenze</code> e "
            "<code>/richiedi</code>.",
            parse_mode=ParseMode.HTML,
            reply_markup=_back(),
        )


def _customer_id(data: str) -> int | None:
    _, _, raw = data.partition(":")
    return int(raw) if raw.isdigit() else None


async def _customer_lines(session, customer_id: int, reseller_id: int):
    overview = await customer_overview(session, customer_id, reseller_id=reseller_id)
    if overview is None or not overview.lines:
        return None
    return overview


def _line_buttons(lines, *, include_menu: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                f"🔁 {line.label or 'Linea unica'} · {line.expires_on:%d/%m}",
                callback_data=f"greq:{line.id}",
            )
        ]
        for line in lines
    ]
    if include_menu:
        rows.append([InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")])
    return InlineKeyboardMarkup(rows)


def _customer_actions(customer_id: int, lines) -> InlineKeyboardMarkup:
    """I tre tasti della scheda cliente, più il ritorno al menu."""
    rows = [[InlineKeyboardButton("📅 Scadenza", callback_data=f"gdue:{customer_id}")]]
    rows.append(
        [
            InlineKeyboardButton("🔑 Codici", callback_data=f"gcod:{customer_id}"),
            InlineKeyboardButton("🔁 Rinnovo", callback_data=f"grenew:{customer_id}"),
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")])
    return InlineKeyboardMarkup(rows)


def _credentials_block(line, domains) -> list[str]:
    """Codici della linea come li espone il pannello, senza inventare niente."""
    from app.bot.codes import _line_block

    return _line_block(line, domains)


async def _show_card(editing, session, data: str, reseller_id: int) -> None:
    """Scheda del cliente senza prezzi: nome, linee e quando scadono."""
    customer_id = _customer_id(data)
    overview = (
        await _customer_lines(session, customer_id, reseller_id)
        if customer_id is not None
        else None
    )
    if overview is None:
        await editing.edit_text("Cliente non disponibile in questo gruppo.", reply_markup=_back())
        return

    today = _today()
    parts = [f"👤 <b>{ui.esc(overview.customer.name)}</b>"]
    if overview.customer.alias:
        parts.append(f'alias: "{ui.esc(overview.customer.alias)}"')
    parts.append("")
    for line in overview.lines:
        parts.append(
            f"• {ui.esc(line.label or 'Linea unica')} — {ui.esc(ui.expiry_phrase(line, today))}"
        )
    await editing.edit_text(
        "\n".join(parts),
        parse_mode=ParseMode.HTML,
        reply_markup=_customer_actions(customer_id, overview.lines),
    )


def _when(line, today) -> str:
    """«fra 44 giorni» / «oggi» / «2 giorni fa», senza ripetere la data."""
    from app.services.months import days_left

    delta = days_left(line.expires_on, today)
    if delta == 0:
        return "oggi"
    if delta == 1:
        return "domani"
    if delta > 1:
        return f"fra {delta} giorni"
    days = abs(delta)
    return f"{days} giorno fa" if days == 1 else f"{days} giorni fa"


async def _show_due(editing, session, data: str, reseller_id: int) -> None:
    """«Scadenza»: le stesse informazioni della scheda, in formato date."""
    customer_id = _customer_id(data)
    overview = (
        await _customer_lines(session, customer_id, reseller_id)
        if customer_id is not None
        else None
    )
    if overview is None:
        await editing.edit_text("Cliente non disponibile in questo gruppo.", reply_markup=_back())
        return

    today = _today()
    parts = [f"📅 <b>Scadenze · {ui.esc(overview.customer.name)}</b>", ""]
    for line in overview.lines:
        parts.append(
            f"• {ui.esc(line.label or 'Linea unica')}: "
            f"<b>{ui.italian_date(line.expires_on)}</b> — "
            f"{ui.esc(_when(line, today))}"
        )
    await editing.edit_text(
        "\n".join(parts),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⬅️ Cliente", callback_data=f"gcard:{customer_id}")],
                [InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")],
            ]
        ),
    )


async def _show_codes(editing, session, data: str, reseller_id: int) -> None:
    """«Codici»: username, password e link delle linee del cliente."""
    from app.services.downloads import load_domains

    customer_id = _customer_id(data)
    overview = (
        await _customer_lines(session, customer_id, reseller_id)
        if customer_id is not None
        else None
    )
    if overview is None:
        await editing.edit_text("Cliente non disponibile in questo gruppo.", reply_markup=_back())
        return

    domains = await load_domains(session)
    parts = [f"🔑 <b>Codici · {ui.esc(overview.customer.name)}</b>", ""]
    for line in overview.lines:
        parts.extend(_credentials_block(line, domains))
        parts.append("")
    parts.append("<i>I valori arrivano dal pannello: quello che non espone resta vuoto.</i>")
    await editing.edit_text(
        "\n".join(parts),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⬅️ Cliente", callback_data=f"gcard:{customer_id}")],
                [InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")],
            ]
        ),
    )


async def _show_customer_renew(editing, session, data: str, reseller_id: int) -> None:
    customer_id = _customer_id(data)
    overview = (
        await _customer_lines(session, customer_id, reseller_id)
        if customer_id is not None
        else None
    )
    if overview is None:
        await editing.edit_text("Cliente non disponibile in questo gruppo.", reply_markup=_back())
        return
    await editing.edit_text(
        f"🔁 <b>Quale linea di {ui.esc(overview.customer.name)}?</b>\n\n"
        "Scegli la linea, poi il periodo: la richiesta parte solo dopo la conferma.",
        parse_mode=ParseMode.HTML,
        reply_markup=_line_buttons(overview.lines),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Testo scritto in gruppo: risponde alla ricerca. True se gestito."""
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None or message.text is None or not is_group(chat.type):
        return False
    if context.user_data.get("awaiting") != SEARCH_HINT:
        return False
    text = message.text.strip()
    if not text:
        return False

    context.user_data.pop("awaiting", None)
    await search_in_group(update, context, text)
    return True


async def search_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Ricerca nel gruppo: solo le linee del rivenditore collegato alla chat.

    Un pulsante per cliente, niente elenco di testo: con molti clienti una lista
    lunga si legge male, e il pulsante è già il «vai a quello lì». Oltre una
    pagina i risultati si sfogliano con ◀️ ▶️, riscrivendo la stessa pulsantiera.

    Usata sia dal pulsante «Cerca cliente» (testo libero) sia da `/cerca nome`,
    che funziona anche con la privacy dei bot attiva.
    """
    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None or not is_group(chat.type):
        return

    async with session_scope() as session:
        found = await _reseller_of_chat(update, session)
        if found is None:
            await message.reply_html("Questo gruppo non è collegato a un rivenditore.")
            return
        reseller_id, _name = found
        lines = await search_lines(session, text, reseller_id=reseller_id, limit=200)

    grouped: dict[int, list] = {}
    for line in lines:
        grouped.setdefault(line.customer_id, []).append(line)
    if not grouped:
        await message.reply_html(
            f"Nessun cliente trovato per «{ui.esc(text)}».",
            reply_markup=_back(),
        )
        return

    entries: list[tuple[int, str]] = []
    for customer_id, customer_lines in grouped.items():
        label = customer_lines[0].customer.name
        if len(customer_lines) > 1:
            label = f"{label} · {len(customer_lines)}"
        entries.append((customer_id, label))

    sent = await message.reply_html(
        _results_text(text, len(entries), 0),
        reply_markup=_results_keyboard(entries, 0),
    )
    _remember_results(chat.id, sent, text, entries)


def _pages(total: int) -> int:
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def _results_text(query: str, total: int, page: int) -> str:
    found_word = "cliente trovato" if total == 1 else "clienti trovati"
    pages = _pages(total)
    where = "" if pages == 1 else f" · pagina {page + 1}/{pages}"
    return f"🔎 <b>Chi è {ui.esc(query)}?</b>\n\n{total} {found_word}{where}: premi il nome."


def _results_keyboard(entries: list[tuple[int, str]], page: int) -> InlineKeyboardMarkup:
    pages = _pages(len(entries))
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    rows = [
        [InlineKeyboardButton(label[:60], callback_data=f"gcard:{customer_id}")]
        for customer_id, label in entries[start : start + PAGE_SIZE]
    ]
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"gpage:{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="gpage:noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"gpage:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton("🔎 Cerca di nuovo", callback_data="gmenu:search")])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="gmenu:home")])
    return InlineKeyboardMarkup(rows)


def _remember_results(chat_id: int, sent, query: str, entries: list[tuple[int, str]]) -> None:
    """Tiene i risultati per la paginazione, legati al messaggio inviato.

    Se il processo si riavvia la cache sparisce: la pagina successiva lo dice e
    invita a cercare di nuovo, invece di mostrare risultati sbagliati.
    """
    message_id = getattr(sent, "message_id", None)
    if message_id is None:
        return
    if len(_RESULTS_CACHE) >= _RESULTS_CACHE_LIMIT:
        _RESULTS_CACHE.pop(next(iter(_RESULTS_CACHE)), None)
    _RESULTS_CACHE[(chat_id, message_id)] = (query, entries)


async def on_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """◀️ ▶️: riscrive lo stesso messaggio con la pagina richiesta."""
    query = update.callback_query
    chat = update.effective_chat
    message = update.effective_message
    if query is None or query.data is None or chat is None or message is None:
        return
    await query.answer()

    data = query.data
    if data == "gpage:noop":
        return
    _, _, raw = data.partition(":")
    if not raw.isdigit():
        return
    page = int(raw)

    message_id = getattr(message, "message_id", None)
    stored = _RESULTS_CACHE.get((chat.id, message_id))
    if stored is None:
        await ui.Editor(query).edit_text(
            "La ricerca è scaduta: cercala di nuovo.", reply_markup=_back()
        )
        return
    search_text, entries = stored

    async with session_scope() as session:
        found = await _reseller_of_chat(update, session)
    if found is None:
        await ui.Editor(query).edit_text("Questo gruppo non è più collegato a un rivenditore.")
        return

    await ui.Editor(query).edit_text(
        _results_text(search_text, len(entries), page),
        parse_mode=ParseMode.HTML,
        reply_markup=_results_keyboard(entries, page),
    )
