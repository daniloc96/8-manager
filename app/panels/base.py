from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.models.enums import Platform
from app.services.text import normalize_key


class PanelError(RuntimeError):
    """Errore di comunicazione o di protocollo con un pannello."""


class PanelAuthError(PanelError):
    """Credenziali rifiutate, 2FA richiesto o sessione non rinnovabile."""


class PanelHTTPError(PanelError):
    """Risposta HTTP inattesa.

    Non conserva il corpo: le risposte possono contenere credenziali.
    """

    def __init__(self, method: str, path: str, status_code: int) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        super().__init__(f"{method} {path} ha risposto {status_code}")


@dataclass(frozen=True, slots=True)
class PanelLine:
    """Fotografia di una linea su un pannello, indipendente dall'API."""

    platform: Platform
    external_id: str
    username: str
    password: str | None = None
    access_code: str | None = None
    expires_on: date | None = None
    status: str | None = None
    note: str | None = None
    max_connections: int | None = None
    package: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def username_key(self) -> str:
        return normalize_key(self.username)


class PanelAdapter(Protocol):
    platform: Platform

    async def fetch_lines(self) -> list[PanelLine]: ...

    async def aclose(self) -> None: ...


def unix_to_date(value: object, tz: str) -> date | None:
    """Timestamp Unix (secondi) del pannello, letto nel fuso dell'owner."""
    if value is None or value == "":
        return None
    try:
        timestamp = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=ZoneInfo(tz)).date()
    except (OverflowError, OSError, ValueError):
        return None


def iso_to_date(value: object, tz: str) -> date | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz)).date()


def to_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
