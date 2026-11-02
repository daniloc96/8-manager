from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.line import Line


class Reseller(TimestampMixin, Base):
    __tablename__ = "resellers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    # Gruppo Telegram del rivenditore: lì il bot risponde alle sue scadenze.
    group_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True)
    group_title: Mapped[str | None] = mapped_column(String(200))
    default_monthly_price_cents: Mapped[int | None] = mapped_column(Integer)
    notify: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    lines: Mapped[list[Line]] = relationship(back_populates="reseller")
