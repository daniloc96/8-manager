from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.line import Line


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    alias: Mapped[str | None] = mapped_column(String(160))
    # nome + alias normalizzati: è la chiave di ricerca principale.
    search_key: Mapped[str] = mapped_column(String(400), nullable=False, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    lines: Mapped[list[Line]] = relationship(
        back_populates="customer", lazy="selectin", order_by="Line.id"
    )
