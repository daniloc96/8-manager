from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from app.config import Settings, get_settings
from app.models.enums import Platform
from app.panels.base import PanelAdapter
from app.panels.eightplus import EightPlusAdapter
from app.panels.lucky import LuckyAdapter
from app.panels.session import session_store_for
from app.panels.snapshots import SnapshotAdapter, load_snapshot

PLATFORM_ALIASES = {
    "8plus": Platform.LIVE_8TVPLUS,
    "lucky": Platform.LIVE_HALLO,
    "vod": Platform.VOD_NEWVOD,
}
ALL_PLATFORMS = (
    Platform.LIVE_8TVPLUS,
    Platform.LIVE_HALLO,
    Platform.VOD_NEWVOD,
)


def select_platforms(names: Sequence[str] | None) -> list[Platform]:
    if not names or "all" in names:
        return list(ALL_PLATFORMS)
    selected: list[Platform] = []
    for name in names:
        try:
            platform = PLATFORM_ALIASES[name]
        except KeyError as exc:
            raise ValueError(f"pannello sconosciuto: {name}") from exc
        if platform not in selected:
            selected.append(platform)
    return selected


def build_adapters(
    *,
    platforms: Sequence[str] | None = None,
    snapshot_dir: Path | None = None,
    settings: Settings | None = None,
) -> tuple[list[PanelAdapter], list[tuple[Platform, str]]]:
    """Costruisce gli adapter configurati; i pannelli incompleti sono saltati."""
    config = settings or get_settings()
    adapters: list[PanelAdapter] = []
    skipped: list[tuple[Platform, str]] = []

    for platform in select_platforms(platforms):
        if snapshot_dir is not None:
            try:
                rows = load_snapshot(platform, snapshot_dir, config.tz)
            except (OSError, ValueError) as exc:
                skipped.append((platform, f"snapshot non utilizzabile: {exc}"))
                continue
            adapters.append(SnapshotAdapter(platform, rows))
            continue

        user, password, base_url = _credentials(config, platform)
        if not user or not password:
            skipped.append((platform, "credenziali mancanti in .env"))
            continue

        if platform is Platform.LIVE_8TVPLUS:
            adapters.append(
                EightPlusAdapter(
                    base_url=base_url,
                    email=user,
                    password=password,
                    tz=config.tz,
                    session_store=session_store_for(platform),
                    timeout=config.panel_timeout_seconds,
                )
            )
        else:
            adapters.append(
                LuckyAdapter(
                    platform=platform,
                    base_url=base_url,
                    username=user,
                    password=password,
                    tz=config.tz,
                    session_store=session_store_for(platform),
                    timeout=config.panel_timeout_seconds,
                )
            )

    return adapters, skipped


def _credentials(settings: Settings, platform: Platform) -> tuple[str, str, str]:
    if platform is Platform.LIVE_8TVPLUS:
        user = settings.panel_8tvplus_user or ""
        password = _secret(settings.panel_8tvplus_pass)
        base_url = settings.panel_8tvplus_base_url
    elif platform is Platform.LIVE_HALLO:
        user = settings.panel_hallo_user or ""
        password = _secret(settings.panel_hallo_pass)
        base_url = settings.panel_hallo_base_url
    else:
        user = settings.panel_newvod_user or ""
        password = _secret(settings.panel_newvod_pass)
        base_url = settings.panel_newvod_base_url
    return user.strip(), password, base_url.strip()


def _secret(value) -> str:
    return value.get_secret_value().strip() if value is not None else ""
