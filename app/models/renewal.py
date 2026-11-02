from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow
from app.models.enums import RenewalSource, enum_column

if TYPE_CHECKING:
    from app.models.line import Line


class Renewal(Base):
    __tablename__ = "renewals"
    __table_args__ = (
        CheckConstraint("months > 0", name="months_positive"),
        CheckConstraint("total_cents >= 0", name="total_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[RenewalSource] = mapped_column(enum_column(RenewalSource), nullable=False)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    line: Mapped[Line] = relationship(back_populates="renewals")
