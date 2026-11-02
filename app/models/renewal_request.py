from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow
from app.models.enums import RequestStatus, enum_column


class RenewalRequest(Base):
    __tablename__ = "renewal_requests"
    __table_args__ = (CheckConstraint("months > 0", name="months_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RequestStatus] = mapped_column(
        enum_column(RequestStatus), default=RequestStatus.PENDING, nullable=False
    )
    origin_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    origin_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    renewal_id: Mapped[int | None] = mapped_column(ForeignKey("renewals.id", ondelete="SET NULL"))
