from __future__ import annotations

from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurazione unica del bot, letta da .env o dall'ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # Telegram
    bot_token: SecretStr | None = None
    owner_telegram_id: int | None = None

    # Runtime
    tz: str = "Europe/Rome"
    log_level: str = "INFO"
    data_dir: Path = Path("/data")
    database_url: str = "sqlite+aiosqlite:////data/8manager.sqlite3"

    # Regole di business
    reminder_days: int = 3
    reminder_hour: int = 9
    price_presets_direct: str = "4,5,8,10"
    duration_presets: str = "1,2,3,4,5,6,7,8,9,10,11,12"
    backup_retention_days: int = 30

    # Pannelli (fase P/S)
    panel_hallo_user: str | None = None
    panel_hallo_pass: SecretStr | None = None
    panel_hallo_base_url: str = "http://netflix.oldadventure.org:25000"
    panel_newvod_user: str | None = None
    panel_newvod_pass: SecretStr | None = None
    panel_newvod_base_url: str = "http://listavod.gostreet.org:2424"
    panel_8tvplus_user: str | None = None
    panel_8tvplus_pass: SecretStr | None = None
    panel_8tvplus_base_url: str = "https://my.8tvplus.com"
    panel_timeout_seconds: float = 20.0
    panel_package_variant: str = ""

    @property
    def direct_price_presets_cents(self) -> tuple[int, ...]:
        return tuple(_parse_price_list(self.price_presets_direct))

    @property
    def duration_presets_list(self) -> tuple[int, ...]:
        values: list[int] = []
        for chunk in self.duration_presets.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                months = int(chunk)
            except ValueError:
                continue
            if 1 <= months <= 36:
                values.append(months)
        return tuple(values) or (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)

    @property
    def heartbeat_path(self) -> Path:
        return self.data_dir / "heartbeat"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    def require_runtime(self) -> tuple[str, int]:
        """Token e owner sono obbligatori solo quando il bot parte davvero."""
        if self.bot_token is None or not self.bot_token.get_secret_value().strip():
            raise RuntimeError("BOT_TOKEN mancante: compila .env")
        if self.owner_telegram_id is None:
            raise RuntimeError("OWNER_TELEGRAM_ID mancante: compila .env")
        return self.bot_token.get_secret_value().strip(), self.owner_telegram_id


def _parse_price_list(raw: str) -> list[int]:
    prices: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip().replace("€", "")
        if not chunk:
            continue
        try:
            value = Decimal(chunk.replace(",", "."))
        except InvalidOperation:
            continue
        cents = int((value * 100).quantize(Decimal("1")))
        if cents >= 0:
            prices.append(cents)
    return prices


@lru_cache
def get_settings() -> Settings:
    return Settings()
