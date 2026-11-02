from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import Service, enum_column

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.panel_line import PanelLine
    from app.models.renewal import Renewal
    from app.models.reseller import Reseller
    from app.models.review_exception import ReviewException


class Line(TimestampMixin, Base):
    __tablename__ = "lines"
    __table_args__ = (
        CheckConstraint("anchor_day BETWEEN 1 AND 31", name="anchor_day_range"),
        CheckConstraint("monthly_price_cents >= 0", name="monthly_price_non_negative"),
        Index("ix_lines_expires_on", "expires_on"),
        Index(
            "uq_lines_customer_label_active",
            "customer_id",
            "label",
            unique=True,
            sqlite_where=text("deleted_at IS NULL AND label IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    label: Mapped[str | None] = mapped_column(String(80))
    reseller_id: Mapped[int | None] = mapped_column(
        ForeignKey("resellers.id", ondelete="SET NULL"), index=True
    )
    service: Mapped[Service] = mapped_column(enum_column(Service), nullable=False)
    monthly_price_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_on: Mapped[date] = mapped_column(Date, nullable=False)
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # Marcata dall'owner dal reminder: la linea non va rinnovata. La verità
    # resta il pannello, quindi la linea sparisce dal bot solo quando la
    # cancellazione remota la fa uscire dal sync.
    no_renewal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    no_renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="lines", lazy="joined")
    reseller: Mapped[Reseller | None] = relationship(back_populates="lines", lazy="joined")
    panel_lines: Mapped[list[PanelLine]] = relationship(
        back_populates="line", lazy="selectin", cascade="all, delete-orphan"
    )
    renewals: Mapped[list[Renewal]] = relationship(
        back_populates="line", lazy="selectin", order_by="Renewal.id"
    )
    review_exception: Mapped[ReviewException | None] = relationship(
        back_populates="line", lazy="selectin", uselist=False
    )
