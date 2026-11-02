from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from app.config import get_settings
from app.services.backup import BackupError, backup_database, sqlite_path

logger = logging.getLogger(__name__)


async def backup_job(_context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    try:
        source = sqlite_path(settings.database_url)
        target = backup_database(source, settings.backup_dir, settings.backup_retention_days)
    except BackupError as exc:
        logger.error("Backup notturno non eseguito: %s", exc)
        return
    except Exception:
        logger.exception("Backup notturno fallito")
        return
    logger.info("Backup notturno: %s", target)
