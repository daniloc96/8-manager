from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from telegram.ext import ContextTypes

from app.paths import resolve_data_dir

logger = logging.getLogger(__name__)


def write_heartbeat() -> Path:
    directory = resolve_data_dir()
    path = directory / "heartbeat"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        datetime.now(UTC).isoformat(timespec="seconds"),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


async def heartbeat_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        write_heartbeat()
    except OSError:
        logger.exception("Impossibile scrivere l'heartbeat")
