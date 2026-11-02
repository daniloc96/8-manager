from __future__ import annotations

import sqlite3
import sys
import time

from sqlalchemy.engine import make_url

from app.config import get_settings
from app.paths import resolve_data_dir

MAX_AGE_SECONDS = 180


def main() -> int:
    settings = get_settings()

    heartbeat = resolve_data_dir() / "heartbeat"
    if not heartbeat.exists():
        print(f"heartbeat mancante: {heartbeat}")
        return 1
    age = time.time() - heartbeat.stat().st_mtime
    if age > MAX_AGE_SECONDS:
        print(f"heartbeat vecchio: {age:.0f}s")
        return 1

    url = make_url(settings.database_url)
    if url.get_backend_name() == "sqlite" and url.database:
        connection = None
        try:
            connection = sqlite3.connect(url.database, timeout=5)
            connection.execute("SELECT 1").fetchone()
        except Exception as exc:
            print(f"database non raggiungibile: {exc}")
            return 1
        finally:
            if connection is not None:
                connection.close()

    print(f"ok (heartbeat {age:.0f}s fa)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
