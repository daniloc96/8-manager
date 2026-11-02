from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Line, PanelLine, Platform, Service
from app.services.text import normalize_key


class PanelLinkError(ValueError):
    """Collegamento manuale non possibile."""


async def link_panel_line(
    session: AsyncSession,
    *,
    platform: Platform,
    username: str,
    line_id: int,
    unlink: bool = False,
) -> tuple[Line, PanelLine]:
    key = normalize_key(username)
    panel_line = await session.scalar(
        select(PanelLine).where(
            PanelLine.platform == platform,
            PanelLine.username_key == key,
        )
    )
    if panel_line is None:
        raise PanelLinkError(f"riga {platform.value}/{username} non trovata")

    line = await session.get(Line, line_id)
    if line is None or line.deleted_at is not None:
        raise PanelLinkError(f"linea {line_id} non trovata o disattivata")

    if unlink:
        panel_line.line_id = None
    else:
        existing = await session.scalar(
            select(PanelLine).where(
                PanelLine.line_id == line.id,
                PanelLine.platform == platform,
                PanelLine.id != panel_line.id,
            )
        )
        if existing is not None:
            raise PanelLinkError(
                f"la linea {line.id} ha già la riga {platform.value}/{existing.username}"
            )
        panel_line.line_id = line.id

    await session.flush()
    await recompute_line_service(session, line)
    return line, panel_line


async def recompute_line_service(session: AsyncSession, line: Line) -> None:
    platforms = set(
        (
            await session.scalars(select(PanelLine.platform).where(PanelLine.line_id == line.id))
        ).all()
    )
    if not platforms:
        if line.service == Service.LIVE_VOD:
            line.service = Service.LIVE
        await session.flush()
        return
    if Platform.VOD_NEWVOD in platforms:
        line.service = Service.LIVE_VOD
    elif Platform.LIVE_HALLO in platforms or Platform.LIVE_8TVPLUS in platforms:
        line.service = Service.LIVE
    await session.flush()
