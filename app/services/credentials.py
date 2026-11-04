from __future__ import annotations

import secrets
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PanelLine
from app.services.text import normalize_key

# Senza vocali e caratteri confondibili: le credenziali si copiano a mano.
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def username_for(name: str, day: date) -> str:
    """Stesso schema del pannello: nome senza spazi più giorno mese anno.

    Le tre parti della data sono sempre a due cifre (`%d%m%y`), quindi l'ID
    resta riconoscibile e confrontabile con quelli già presenti.
    """
    slug = "".join(ch for ch in normalize_key(name) if ch.isalnum())[:14]
    return f"{slug}{day:%d%m%y}"


async def ensure_username(session: AsyncSession, base: str) -> str:
    """Username libero rispetto a quelli già visti nei pannelli."""
    candidate = base
    suffix = 2
    while (
        await session.scalar(select(PanelLine.id).where(PanelLine.username_key == candidate))
        is not None
    ):
        candidate = f"{base}{suffix}"
        suffix += 1
        if suffix > 99:
            raise ValueError("troppi username simili: controlla il pannello.")
    return candidate


async def unique_username(session: AsyncSession, name: str, day: date) -> str:
    return await ensure_username(session, username_for(name, day))


def password(length: int = 8) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
