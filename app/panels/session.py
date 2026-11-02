from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.enums import Platform
from app.paths import resolve_data_dir


@dataclass(slots=True)
class SessionStore:
    """Cache locale di cookie/JWT, fuori dal database e fuori da git."""

    path: Path

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            self.path.parent.chmod(0o700)

        temporary = self.path.with_name(f"{self.path.name}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def session_store_for(platform: Platform) -> SessionStore:
    return SessionStore(resolve_data_dir() / "panel_sessions" / f"{platform.value}.json")
