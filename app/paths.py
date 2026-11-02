from pathlib import Path

from app.config import get_settings


def resolve_data_dir() -> Path:
    """Cartella dati scrivibile.

    In container è /data (volume). In locale, se /data non è scrivibile,
    si ripiega su ./data senza far fallire l'avvio.
    """
    settings = get_settings()
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        return settings.data_dir
    except OSError:
        fallback = Path("data")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
