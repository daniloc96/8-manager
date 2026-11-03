from __future__ import annotations

import json
from pathlib import Path

from app.models.enums import Platform
from app.panels.base import PanelLine
from app.panels.eightplus import eightplus_row_to_panel_line
from app.panels.lucky import lucky_row_to_panel_line

SNAPSHOT_PATTERNS = {
    Platform.LIVE_8TVPLUS: "panel-8plus-*.json",
    Platform.LIVE_HALLO: "panel-lucky-live-*.json",
    Platform.VOD_NEWVOD: "panel-vod-*.json",
}


class SnapshotAdapter:
    """Adapter offline: usa gli snapshot della discovery invece della rete."""

    def __init__(self, platform: Platform, rows: list[PanelLine]) -> None:
        self.platform = platform
        self._rows = rows

    async def fetch_lines(self) -> list[PanelLine]:
        return list(self._rows)

    async def fetch_download_domain(self) -> str | None:
        # Gli snapshot della discovery non contengono /lines/meta.
        return None

    async def aclose(self) -> None:
        return None


def load_snapshot(platform: Platform, directory: Path, tz: str) -> list[PanelLine]:
    pattern = SNAPSHOT_PATTERNS[platform]
    paths = sorted(directory.glob(pattern))
    if not paths:
        raise FileNotFoundError(f"nessuno snapshot {pattern} in {directory}")

    payload = json.loads(paths[-1].read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{paths[-1]}: snapshot inatteso")

    if platform is Platform.LIVE_8TVPLUS:
        raw_rows = payload.get("rows") or []
        converted = [
            eightplus_row_to_panel_line(row, tz) for row in raw_rows if isinstance(row, dict)
        ]
    else:
        container = payload.get("data")
        raw_rows = container.get("data") if isinstance(container, dict) else None
        if raw_rows is None:
            raw_rows = payload.get("rows") or []
        converted = [
            lucky_row_to_panel_line(row, platform, tz) for row in raw_rows if isinstance(row, dict)
        ]

    return [line for line in converted if line is not None]
