from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.line import Line


class ReviewException(TimestampMixin, Base):
    """Anomalia accettata su una linea.

    La review sparisce dal sync finché `review_on` è nel futuro; quando la data
    passa l'anomalia torna a essere segnalata. `review_on` nullo = per sempre.
    """

    __tablename__ = "review_exceptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    line_id: Mapped[int] = mapped_column(
        ForeignKey("lines.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    review_on: Mapped[date | None] = mapped_column(Date)

    line: Mapped[Line] = relationship(back_populates="review_exception")
