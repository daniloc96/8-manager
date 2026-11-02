from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Reseller

GROUP_CHAT_TYPES = {"group", "supergroup"}


@dataclass(frozen=True, slots=True)
class Access:
    """Chi sta scrivendo: owner, rivenditore collegato, oppure nessuno.

    `via_group` è True quando il permesso di lettura arriva dal gruppo
    registrato e non dall'ID Telegram di chi scrive.
    """

    kind: str
    reseller_id: int | None = None
    name: str = ""
    via_group: bool = False

    @property
    def is_owner(self) -> bool:
        return self.kind == "owner"

    @property
    def is_reseller(self) -> bool:
        return self.kind == "reseller"

    @property
    def allowed(self) -> bool:
        return self.kind in {"owner", "reseller"}

    @property
    def can_act(self) -> bool:
        """Può modificare qualcosa: serve l'identità personale, non il gruppo."""
        return self.kind == "owner" or (self.kind == "reseller" and not self.via_group)


def is_owner(telegram_user_id: int | None) -> bool:
    if telegram_user_id is None:
        return False
    owner_id = get_settings().owner_telegram_id
    return owner_id is not None and telegram_user_id == owner_id


def is_group(chat_type: str | None) -> bool:
    return chat_type in GROUP_CHAT_TYPES


async def resolve_access(
    session: AsyncSession,
    telegram_user_id: int | None,
    *,
    chat_id: int | None = None,
    chat_type: str | None = None,
) -> Access:
    if telegram_user_id is not None and is_owner(telegram_user_id):
        return Access(kind="owner", name="owner")

    if telegram_user_id is not None:
        reseller = await session.scalar(
            select(Reseller).where(
                Reseller.telegram_user_id == telegram_user_id,
                Reseller.active.is_(True),
            )
        )
        if reseller is not None:
            return Access(kind="reseller", reseller_id=reseller.id, name=reseller.name)

    if chat_id is not None and is_group(chat_type):
        group_reseller = await session.scalar(
            select(Reseller).where(
                Reseller.group_chat_id == chat_id,
                Reseller.active.is_(True),
            )
        )
        if group_reseller is not None:
            return Access(
                kind="reseller",
                reseller_id=group_reseller.id,
                name=group_reseller.name,
                via_group=True,
            )

    return Access(kind="none")


async def access_for(update, session: AsyncSession) -> Access:
    """Accesso a partire da un Update Telegram (utente + chat)."""
    user = update.effective_user
    chat = update.effective_chat
    return await resolve_access(
        session,
        user.id if user else None,
        chat_id=chat.id if chat else None,
        chat_type=chat.type if chat else None,
    )
