from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow
from app.models.enums import RequestStatus, Service, enum_column


class NewLineRequest(Base):
    """Richiesta di una linea nuova da parte di un rivenditore.

    Non nasce da una linea esistente: `username` è l'ID definitivo calcolato
    alla richiesta (nome + giorno/mese/anno a due cifre), così non cambia se
    l'owner conferma qualche giorno dopo.
    """

    __tablename__ = "new_line_requests"
    __table_args__ = (CheckConstraint("months > 0", name="months_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reseller_id: Mapped[int] = mapped_column(
        ForeignKey("resellers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    months: Mapped[int] = mapped_column(Integer, nullable=False)
    service: Mapped[Service] = mapped_column(enum_column(Service), nullable=False)
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
    # Quando i codici sono stati mandati al gruppo: evita doppi invii sul retry.
    announced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    line_id: Mapped[int | None] = mapped_column(ForeignKey("lines.id", ondelete="SET NULL"))
