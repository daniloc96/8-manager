from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.engine import make_url


class BackupError(RuntimeError):
    """Il database non è copiabile con l'API sqlite3."""


def sqlite_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise BackupError("backup supportato solo per SQLite")
    return Path(url.database)


def backup_database(source: Path, backup_dir: Path, retention_days: int) -> Path:
    """Copia consistente con l'API sqlite3 e applica la retention."""
    if not source.exists():
        raise BackupError(f"database non trovato: {source}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = backup_dir / f"8manager-{stamp}.sqlite3"

    src = sqlite3.connect(source)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    target.chmod(0o600)

    backups = sorted(backup_dir.glob("8manager-*.sqlite3"))
    for old in backups[:-retention_days]:
        old.unlink(missing_ok=True)

    return target
