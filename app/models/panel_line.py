from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import Platform, enum_column

if TYPE_CHECKING:
    from app.models.line import Line


class PanelLine(TimestampMixin, Base):
    """Stato di una linea su un pannello esterno.

    line_id nullo = riga trovata nel pannello ma non ancora classificata.
    Una linea locale può avere zero, una o due righe (live + VOD).
    """

    __tablename__ = "panel_lines"
    __table_args__ = (
        UniqueConstraint("platform", "username_key", name="uq_panel_lines_platform_username"),
        Index(
            "uq_panel_lines_line_platform",
            "line_id",
            "platform",
            unique=True,
            sqlite_where=text("line_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int | None] = mapped_column(
        ForeignKey("lines.id", ondelete="SET NULL"), index=True
    )
    platform: Mapped[Platform] = mapped_column(enum_column(Platform, 30), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(120))
    username: Mapped[str] = mapped_column(String(160), nullable=False)
    username_key: Mapped[str] = mapped_column(String(160), nullable=False)
    password: Mapped[str | None] = mapped_column(String(160))
    access_code: Mapped[str | None] = mapped_column(String(160))
    expires_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(String(40))
    package: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(500))
    raw_json: Mapped[dict | None] = mapped_column(JSON)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    line: Mapped[Line | None] = relationship(back_populates="panel_lines")
