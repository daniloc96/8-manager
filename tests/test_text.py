from __future__ import annotations

from app.services.text import (
    customer_search_key,
    format_eur,
    normalize_key,
    parse_price_to_cents,
)


def test_normalize_key_strips_accents_and_case() -> None:
    assert normalize_key("Città di Mario") == "citta di mario"
    assert normalize_key("  Mario   ROSSI ") == "mario rossi"
    assert normalize_key(None) == ""
    assert normalize_key("") == ""


def test_customer_search_key_includes_alias() -> None:
    assert customer_search_key("Mario Rossi", "Il Rosso") == "mario rossi il rosso"
    assert customer_search_key("Mario Rossi") == "mario rossi"


def test_format_eur() -> None:
    assert format_eur(0) == "0,00 €"
    assert format_eur(500) == "5,00 €"
    assert format_eur(123456) == "1.234,56 €"
    assert format_eur(-1050) == "-10,50 €"


def test_parse_price_to_cents() -> None:
    assert parse_price_to_cents("5") == 500
    assert parse_price_to_cents("5,00") == 500
    assert parse_price_to_cents("5.00") == 500
    assert parse_price_to_cents("€ 8") == 800
    assert parse_price_to_cents("1.234,56") == 123456
    assert parse_price_to_cents("") is None
    assert parse_price_to_cents(None) is None
    assert parse_price_to_cents("abc") is None
    assert parse_price_to_cents("-3") is None
