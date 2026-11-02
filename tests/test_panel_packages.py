from __future__ import annotations

from app.models import Platform
from app.panels.packages import (
    eightplus_duration_days,
    eightplus_supported_months,
    package_months,
    resolve_package,
)


def test_package_months_reads_italian_and_english_names() -> None:
    assert package_months("3 MESI (MONTH) - (All Package)") == 3
    assert package_months("12 Month / 12 Mesi") == 12
    assert package_months("1 MESE (MONTH) - (No Hot Club)") == 1
    assert package_months("Multivision") is None
    assert package_months("Pacchetto senza durata") is None


def test_resolve_package_prefers_plain_all_package() -> None:
    packages = [
        {"id": 1, "name": "3 MESI (MONTH) - (No Hot Club)"},
        {"id": 2, "name": "3 MESI (MONTH) - (All Package)"},
        {"id": 3, "name": "12 MESI (MONTH) - (All Package)"},
    ]
    chosen = resolve_package(packages, 3, platform=Platform.VOD_NEWVOD)
    assert chosen is not None
    assert chosen["id"] == 2


def test_resolve_package_honours_variant() -> None:
    packages = [
        {"id": 1, "name": "3 MESI (MONTH) - (All Package)"},
        {"id": 2, "name": "3 MESI (MONTH) - (No Hot Club)"},
    ]
    chosen = resolve_package(packages, 3, platform=Platform.VOD_NEWVOD, variant="No Hot Club")
    assert chosen is not None
    assert chosen["id"] == 2


def test_resolve_package_falls_back_to_known_id() -> None:
    packages = [{"id": 41, "name": "Pacchetto senza durata"}]
    chosen = resolve_package(packages, 1, platform=Platform.VOD_NEWVOD)
    assert chosen is not None
    assert chosen["id"] == 41


def test_resolve_package_returns_none_for_unknown_month() -> None:
    packages = [{"id": 41, "name": "1 MESE (MONTH) - (All Package)"}]
    assert resolve_package(packages, 2, platform=Platform.VOD_NEWVOD) is None


def test_eightplus_uses_days_not_months() -> None:
    assert eightplus_duration_days(1) == 30
    assert eightplus_duration_days(3) == 90
    assert eightplus_duration_days(12) == 365
    assert eightplus_duration_days(2) is None
    assert eightplus_duration_days(5) is None
    assert eightplus_supported_months() == (1, 3, 6, 12)
