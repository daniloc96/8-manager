from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_settings_require_runtime() -> None:
    settings = Settings(_env_file=None, bot_token="123:abc", owner_telegram_id=42)
    token, owner = settings.require_runtime()
    assert token == "123:abc"
    assert owner == 42


def test_settings_missing_runtime_raises() -> None:
    settings = Settings(_env_file=None, bot_token=None, owner_telegram_id=None)
    with pytest.raises(RuntimeError):
        settings.require_runtime()


def test_settings_presets() -> None:
    settings = Settings(
        _env_file=None,
        price_presets_direct="4, 5, 8, 10",
        duration_presets="1,2,3,5,12",
    )
    assert settings.direct_price_presets_cents == (400, 500, 800, 1000)
    assert settings.duration_presets_list == (1, 2, 3, 5, 12)


def test_settings_duration_fallback() -> None:
    settings = Settings(_env_file=None, duration_presets="0,99,abc")
    assert settings.duration_presets_list == (1, 2, 3, 5, 12)


def test_settings_invalid_owner_id() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, owner_telegram_id="non-un-numero")
