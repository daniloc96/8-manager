from __future__ import annotations

from datetime import date

from app.bot import ui
from app.models import Customer, Line, PanelLine, Platform, Reseller, Service
from app.services.text import customer_search_key

TODAY = date(2026, 11, 2)


def make_line(**kwargs) -> Line:
    customer = kwargs.pop(
        "customer",
        Customer(name="Mario Caterino", alias="Mario il rosso", search_key="mario caterino"),
    )
    return Line(
        customer=customer,
        service=kwargs.pop("service", Service.LIVE_VOD),
        monthly_price_cents=kwargs.pop("monthly_price_cents", 500),
        started_on=date(2025, 1, 1),
        expires_on=kwargs.pop("expires_on", date(2026, 11, 13)),
        anchor_day=1,
        **kwargs,
    )


def test_line_card_shows_service_price_and_panels() -> None:
    line = make_line()
    line.panel_lines = [
        PanelLine(platform=Platform.LIVE_HALLO, username="mario2026", username_key="mario2026"),
        PanelLine(platform=Platform.VOD_NEWVOD, username="mario-vod", username_key="mario-vod"),
    ]

    card = ui.line_card(line, TODAY)

    assert "Mario Caterino" in card
    assert "alias" in card and "Mario il rosso" in card
    assert "Live + VOD" in card
    assert "5,00 €" in card
    assert "mario2026" in card and "mario-vod" in card
    assert "13/11/2026" in card


def test_line_card_marks_missing_price_and_label() -> None:
    line = make_line(monthly_price_cents=0, label="Linea 2")

    card = ui.line_card(line, TODAY)

    assert "Linea 2" in card
    assert "prezzo da impostare" in card


def test_expiry_phrase_handles_today_and_past() -> None:
    assert "oggi" in ui.expiry_phrase(make_line(expires_on=TODAY), TODAY)
    assert "domani" in ui.expiry_phrase(make_line(expires_on=date(2026, 11, 3)), TODAY)
    assert "scaduta" in ui.expiry_phrase(make_line(expires_on=date(2026, 10, 1)), TODAY)
    assert "tra 11 giorni" in ui.expiry_phrase(make_line(expires_on=date(2026, 11, 13)), TODAY)


def test_month_keyboard_contains_presets_and_other() -> None:
    line = make_line()
    line.id = 7

    markup = ui.months_keyboard(line, (1, 2, 3))

    data = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert "renew:7:1" in data
    assert "renew:7:2" in data
    assert "renew:7:3" in data
    assert "renew:7:other" in data
    assert "card:7" in data


def test_line_keyboard_owner_actions() -> None:
    line = make_line()
    line.id = 7

    owner_markup = ui.line_keyboard(line, is_owner=True)
    owner_data = [b.callback_data for row in owner_markup.inline_keyboard for b in row]
    assert "renew:7:pick" in owner_data
    assert "price:7" in owner_data
    assert all("off:" not in item for item in owner_data)
    assert "history:7" in owner_data

    guest_markup = ui.line_keyboard(line, is_owner=False)
    guest_data = [b.callback_data for row in guest_markup.inline_keyboard for b in row]
    assert all("price:" not in item and "off:" not in item for item in guest_data)


def test_line_title_uses_label_or_unica() -> None:
    with_label = make_line(label="Linea 3")
    assert ui.line_title(with_label) == "Mario Caterino · Linea 3"

    without_label = make_line()
    without_label.customer = Customer(name="Anna Bianchi", search_key="anna bianchi")
    assert ui.line_title(without_label) == "Anna Bianchi · Linea unica"


def test_menu_keyboard_hides_owner_sections_for_reseller() -> None:
    owner_data = [
        b.callback_data for row in ui.menu_keyboard(is_owner=True).inline_keyboard for b in row
    ]
    assert "menu:income" in owner_data
    assert "menu:resellers" in owner_data

    guest_data = [
        b.callback_data for row in ui.menu_keyboard(is_owner=False).inline_keyboard for b in row
    ]
    assert "menu:income" not in guest_data
    assert "menu:due" in guest_data


def test_line_row_includes_price_and_expiry() -> None:
    line = make_line()
    row = ui.line_row(line, TODAY)
    assert "Mario Caterino" in row
    assert "13/11/2026" in row
    assert "5,00 €" in row


def test_reseller_label_on_card() -> None:
    line = make_line(reseller=Reseller(name="Luca Rossi", name_key="luca rossi"))
    assert "Luca Rossi" in ui.line_card(line, TODAY)
    assert "Diretto" not in ui.line_card(line, TODAY)


def test_line_card_shows_download_links() -> None:
    line = make_line()
    line.panel_lines = [
        PanelLine(
            platform=Platform.LIVE_HALLO,
            username="mario-live",
            username_key="mario-live",
            password="live-pass",
        ),
        PanelLine(
            platform=Platform.VOD_NEWVOD,
            username="mario-vod",
            username_key="mario-vod",
            password="vod-pass",
        ),
    ]

    card = ui.line_card(
        line,
        TODAY,
        [
            ("Live", "http://hulu.test/get.php?username=mario-live&password=live-pass"),
            ("VOD", "http://vod.test/get.php?username=mario-vod&password=vod-pass"),
        ],
    )

    assert "Live: <code>http://hulu.test" in card
    assert "VOD: <code>http://vod.test" in card
    assert "&amp;password=live-pass" in card


def test_line_card_uses_code_label_for_8plus() -> None:
    line = make_line(service=Service.LIVE)
    line.panel_lines = [
        PanelLine(
            platform=Platform.LIVE_8TVPLUS,
            username="CODE1",
            username_key="code1",
            access_code="CODE1",
        )
    ]

    card = ui.line_card(line, TODAY)

    assert "Codice: <code>CODE1</code>" in card
    assert "Live: <code>CODE1</code>" not in card


def test_due_keyboard_reseller_can_open_card_and_request() -> None:
    line = make_line()
    line.id = 7

    markup = ui.due_keyboard([line], is_owner=False)
    data = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert "card:7" in data
    assert "rq:7" in data


def test_due_keyboard_owner_can_renew_and_open_card() -> None:
    line = make_line()
    line.id = 7

    markup = ui.due_keyboard([line], is_owner=True)
    data = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert "renew:7:pick" in data
    assert "card:7" in data
    assert "rq:7" not in data


def test_customer_search_key_used_in_ui_does_not_crash() -> None:
    customer = Customer(name="Cafè", search_key=customer_search_key("Cafè"))
    line = make_line(customer=customer)
    assert "Cafè" in ui.line_card(line, TODAY)
