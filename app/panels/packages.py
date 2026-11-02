from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.models.enums import Platform
from app.services.text import normalize_key

MONTH_PATTERN = re.compile(
    r"(?<![\d.,])(\d{1,2})\s*(?:mes[ei]|month|months?)\b",
    re.IGNORECASE,
)

# Varianti che non sono il pacchetto standard: restano disponibili, ma non
# vengono scelte per default.
VARIANT_MARKERS = (
    "no hot",
    "xxx",
    "lista ridotta",
    "ridotta",
    "multivision",
    "+ hot",
    "hot club",
)

# 8Plus lavora a giorni, non a mesi di calendario. 2 e 5 mesi non esistono.
EIGHTPLUS_DAYS_BY_MONTHS = {1: 30, 3: 90, 6: 180, 12: 365}

# Solo se il nome non risolve: gli ID possono cambiare da un giorno all'altro.
FALLBACK_PACKAGE_IDS = {
    Platform.VOD_NEWVOD: {1: 41, 2: 83, 3: 42, 5: 85, 12: 44},
    Platform.LIVE_HALLO: {1: 5, 2: 6, 3: 7, 5: 9, 12: 16},
}


def package_months(name: str | None) -> int | None:
    """Mesi dichiarati nel nome del pacchetto ("3 MESI", "12 Month")."""
    if not name:
        return None
    match = MONTH_PATTERN.search(name)
    if match is None:
        return None
    months = int(match.group(1))
    return months if 1 <= months <= 36 else None


def is_plain_package(name: str | None) -> bool:
    key = normalize_key(name)
    return bool(key) and not any(marker in key for marker in VARIANT_MARKERS)


def resolve_package(
    packages: Sequence[Mapping[str, Any]],
    months: int,
    *,
    platform: Platform,
    variant: str | None = None,
) -> Mapping[str, Any] | None:
    """Sceglie il pacchetto di N mesi per nome, non per ID."""
    candidates: list[tuple[tuple[int, int, int], Mapping[str, Any]]] = []
    for package in packages:
        name = str(package.get("name") or "")
        if package_months(name) != months:
            continue
        candidates.append((_score(name, variant), package))

    if candidates:
        return min(candidates, key=lambda candidate: candidate[0])[1]

    fallback = FALLBACK_PACKAGE_IDS.get(platform, {}).get(months)
    if fallback is None:
        return None
    for package in packages:
        if _as_int(package.get("id")) == fallback:
            return package
    return None


def eightplus_duration_days(months: int) -> int | None:
    return EIGHTPLUS_DAYS_BY_MONTHS.get(months)


def eightplus_supported_months() -> tuple[int, ...]:
    return tuple(sorted(EIGHTPLUS_DAYS_BY_MONTHS))


def _score(name: str, variant: str | None) -> tuple[int, int, int]:
    key = normalize_key(name)
    if variant and normalize_key(variant) in key:
        return (0, 0, len(name))
    if is_plain_package(name):
        return (1, 0 if "all package" in key else 1, len(name))
    return (2, 0, len(name))


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
