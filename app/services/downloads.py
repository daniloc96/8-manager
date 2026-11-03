"""Domini di download dei pannelli e link per le singole linee.

Il dominio è globale per pannello: cambia solo se il provider cambia host,
non per cliente. La sync lo legge da `/lines/meta` e lo salva in `settings`.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Line, Platform, Setting
from app.services.queries import panel_for
from app.services.vod import download_link

DOMAIN_SETTING_KEYS: dict[Platform, str] = {
    Platform.LIVE_HALLO: "live_domain",
    Platform.VOD_NEWVOD: "vod_domain",
}


def normalize_domain(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().rstrip("/")
    return text or None


async def read_domain(session: AsyncSession, platform: Platform) -> str | None:
    key = DOMAIN_SETTING_KEYS.get(platform)
    if key is None:
        return None
    setting = await session.get(Setting, key)
    return normalize_domain(setting.value) if setting is not None else None


async def load_domains(session: AsyncSession) -> dict[Platform, str]:
    """Domini salvati, per piattaforma, senza le chiavi non ancora valorizzate."""
    domains: dict[Platform, str] = {}
    for platform in DOMAIN_SETTING_KEYS:
        value = await read_domain(session, platform)
        if value:
            domains[platform] = value
    return domains


async def save_domain(
    session: AsyncSession,
    platform: Platform,
    domain: str | None,
    *,
    apply: bool = True,
) -> bool:
    """Allinea il dominio globale del pannello. True se il valore è cambiato.

    Con `apply=False` (dry-run) calcola la differenza senza scrivere.
    """
    key = DOMAIN_SETTING_KEYS.get(platform)
    normalized = normalize_domain(domain)
    if key is None or normalized is None:
        return False

    setting = await session.get(Setting, key)
    changed = setting is None or setting.value != normalized
    if not changed or not apply:
        return changed

    if setting is None:
        session.add(Setting(key=key, value=normalized))
    else:
        setting.value = normalized
    return True


def line_downloads(line: Line, domains: Mapping[Platform, str]) -> list[tuple[str, str]]:
    """Coppie (etichetta, link) per le righe di pannello collegate alla linea."""
    links: list[tuple[str, str]] = []

    live = panel_for(line, Platform.LIVE_HALLO)
    if live is not None:
        url = download_link(domains.get(Platform.LIVE_HALLO), live.username, live.password)
        if url:
            links.append(("Live", url))

    vod = panel_for(line, Platform.VOD_NEWVOD)
    if vod is not None:
        url = download_link(domains.get(Platform.VOD_NEWVOD), vod.username, vod.password)
        if url:
            links.append(("VOD", url))

    return links
