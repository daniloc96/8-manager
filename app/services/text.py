from __future__ import annotations

import unicodedata
from decimal import Decimal, InvalidOperation


def normalize_key(value: str | None) -> str:
    """Minuscolo, senza accenti, spazi compattati: la forma su cui si cerca."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(stripped.casefold().split())


def customer_search_key(name: str, alias: str | None = None) -> str:
    return normalize_key(f"{name} {alias or ''}")


def format_eur(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    euros, remainder = divmod(abs(cents), 100)
    thousands = f"{euros:,}".replace(",", ".")
    return f"{sign}{thousands},{remainder:02d} €"


def parse_price_to_cents(value: str | None) -> int | None:
    """Accetta "5", "5,00", "5.00", "€ 5" e restituisce i centesimi."""
    if value is None:
        return None
    cleaned = value.strip().replace("€", "").replace(" ", "")
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        # "1.234,56": il punto è separatore delle migliaia.
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if amount < 0:
        return None
    return int((amount * 100).quantize(Decimal("1")))
