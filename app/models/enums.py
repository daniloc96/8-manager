from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


class Service(StrEnum):
    LIVE = "live"
    LIVE_VOD = "live_vod"


class Platform(StrEnum):
    LIVE_HALLO = "live_hallo"
    VOD_NEWVOD = "vod_newvod"
    LIVE_8TVPLUS = "live_8tvplus"


class LineStatus(StrEnum):
    ACTIVE = "active"
    EXPIRING = "expiring"
    EXPIRED = "expired"


class RenewalSource(StrEnum):
    REMINDER = "reminder"
    CARD = "card"
    REQUEST = "request"
    SYNC = "sync"
    MANUAL = "manual"
    IMPORT = "import"


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


def enum_column(enum_cls: type[StrEnum], length: int = 20) -> SAEnum:
    """VARCHAR con i valori leggibili ("live_vod"), non i nomi Python."""
    return SAEnum(
        enum_cls,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        length=length,
    )
